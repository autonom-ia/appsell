import { NextRequest, NextResponse, after } from "next/server";

import { isChargeableBuyerEmail, normalizeBuyerEmail } from "@/lib/buyer-email";
import { resolveBuyerIdentity } from "@/lib/buyer-identity.server";
import { STRIPE_CHECKOUT_CURRENCY, resolveCheckoutProduct } from "@/lib/checkout-config";
import { DEFAULT_CHECKOUT_PLAN, directChargeApplicationFeeCents } from "@/lib/checkout-fee";
import { gravarPedidoDoCheckout, ligarPedidoAoIntent, type PedidoGravado } from "@/lib/checkout-order.server";
import { pedidoPinadoLiberado } from "@/lib/checkout-order-pilot";
import { connectCheckoutIdempotencyKey, isFinancialConnectSeller } from "@/lib/financial-connect-checkout";
import {
  checkoutProvider,
  checkoutWebhookEndpointKey,
  createFinancialExternalCheckoutIntent,
  createFinancialExternalCheckoutSession,
  FinancialExternalCheckoutConfigError,
  FinancialExternalCheckoutError
} from "@/lib/financial-external-checkout.server";
import {
  DomainApiError,
  readPublicStorefront,
  resolveCheckoutPayoutDestination
} from "@/lib/internal-api-client.server";
import { RawBodyTooLargeError, readLimitedRawBody } from "@/lib/limited-raw-body.server";
import { checkSellerNet } from "@/lib/minimum-price";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { parsePublicStorefront, type PublicStorefrontProduct } from "@/lib/public-storefront";
import { StripeConfigError, stripeClient } from "@/lib/stripe-config.server";
import { atribuicaoFalhou, registrarAtribuicaoDoCheckout } from "@/lib/tracking/checkout-attribution.server";

// Stripe refuses expires_at under 30 minutes from ITS creation time, which is
// later than ours by the request latency; one extra minute keeps us above it.
const CHECKOUT_SESSION_TTL_SECONDS = 31 * 60;

export const dynamic = "force-dynamic";

// Slice 1 (platform-direct, NOT Connect): create a REAL one-time Stripe Checkout
// Session that charges the platform's own Stripe account in BRL. The buyer
// browser posts only identifiers + e-mail; the amount is NEVER trusted from the
// request. It is read server-side from the domain's public storefront
// projection (offer.unit_amount_minor, migration 0018) exposed by
// readPublicStorefront — the same source the storefront page renders. The
// generated order_id + product key travel in Stripe session metadata so the
// paired webhook (app/api/webhooks/stripe) can self-emit the signed
// purchase.approved event that grants access.

const MAX_BODY_BYTES = 2_048;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

interface CreateSessionInput {
  sellerSlug: string;
  productSlug: string;
  buyerEmail: string;
}

// The alert for a listed store that cannot sell. Seller id only: the account
// id stays out of the log.
function logConnectSellerNotReady(
  sellerId: string,
  destination: { charges_enabled: boolean; payouts_enabled?: boolean }
) {
  console.warn(
    JSON.stringify({
      marker: "appsell.connect.seller_not_ready",
      seller_id: sellerId,
      charges_enabled: destination.charges_enabled === true,
      payouts_enabled: destination.payouts_enabled === true
    })
  );
}

export async function POST(request: NextRequest) {
  // Same-origin guard (defense against cross-site POST): this endpoint mints a
  // payment session, so reject anything that is not the configured public origin.
  if (!hasConfiguredOrigin(request, configuredPublicOrigin())) return jsonError("forbidden_origin", 403);

  let rawBody: Buffer;
  try {
    rawBody = await readLimitedRawBody(request, MAX_BODY_BYTES);
  } catch (error) {
    return error instanceof RawBodyTooLargeError ? jsonError("payload_too_large", 413) : jsonError("invalid_request", 400);
  }

  const input = parseCreateSessionInput(rawBody);
  if (!input) return jsonError("invalid_request", 400);

  let provider: ReturnType<typeof checkoutProvider>;
  try {
    provider = checkoutProvider();
  } catch (error) {
    if (error instanceof FinancialExternalCheckoutConfigError) return jsonError("checkout_not_configured", 503);
    throw error;
  }
  const mapping = resolveCheckoutProduct(input.sellerSlug, input.productSlug);
  if (provider === "local_stripe" && !mapping) return jsonError("product_not_configured", 409);

  let storefront: unknown;
  try {
    storefront = await readPublicStorefront(input.sellerSlug);
  } catch (error) {
    if (error instanceof DomainApiError && error.status === 404) return jsonError("seller_not_found", 404);
    return jsonError("storefront_unavailable", 503);
  }

  const parsed = parsePublicStorefront(storefront);
  if (!parsed) return jsonError("storefront_unavailable", 503);
  const product = parsed.products.find((candidate) => candidate.slug === input.productSlug);
  if (!product) return jsonError("product_not_found", 404);

  const amount = purchasableOneTimeAmount(product);
  if (amount === null) return jsonError("product_not_purchasable", 409);

  if (provider === "financial_checkout" &&
      (!parsed.seller.financial_operator_id || !parsed.seller.financial_product_catalog_item_id)) {
    return jsonError("seller_financial_not_configured", 409);
  }

  // AUTHENTICATION IS REQUIRED TO CHARGE, and it is a money rule, not a UX one.
  //
  // Migration 0020's worker owns the entitlement as
  // identity_user_id(issuer, coalesce(buyer_identity_sub,
  // 'registration:' || buyer_email)). With no subject, the grant lands on the
  // synthetic registration:<e-mail> identity — and this repo has NO path that
  // lets a real login claim such a grant. So an anonymous Stripe checkout would
  // take the money and produce access nobody can reach.
  //
  // Every non-authenticated state therefore fails BEFORE the Stripe call, each
  // with its own status so the cause is not guesswork: a session store outage
  // must not be silently downgraded to "anonymous". Anonymous purchase is a
  // later slice, and needs the claim flow first (Refs #146).
  const identity = await resolveBuyerIdentity(request, input.buyerEmail);
  if (identity.status === "unavailable") return jsonError("session_unavailable", 503);
  if (identity.status === "anonymous") return jsonError("authentication_required", 401);
  if (identity.status === "mismatch") return jsonError("buyer_email_mismatch", 409);
  const buyerIdentitySub = identity.subject;

  // Financial owns gateway selection from the product's operator. The payout
  // destination below belongs to the legacy local direct-charge path and, on
  // the Financial path, only to the stores listed for their own connected
  // account (#440); every other Financial store sends no account.
  const connectSeller = provider === "financial_checkout" && isFinancialConnectSeller(parsed.seller.id);
  let destination: { provider_account_id: string; charges_enabled: boolean; payouts_enabled?: boolean } | null = null;
  if (provider !== "financial_checkout" || connectSeller) {
    try {
      destination = (await resolveCheckoutPayoutDestination(parsed.seller.id)).payout_destination;
    } catch {
      return jsonError("payout_destination_unavailable", 503);
    }
    if (destination && !destination.charges_enabled) {
      return jsonError("seller_not_ready_to_charge", 409);
    }
    // A listed store charges on its own account or not at all: falling back to
    // the operator's gateway would put the money in the wrong account.
    if (connectSeller && !destination) return jsonError("seller_not_ready_to_charge", 409);
    // #545: a listed store sells only with a VERIFIED account, charges AND
    // payouts. Checked on every sale, so an account that loses verification
    // after activation stops selling on the next purchase.
    if (connectSeller && destination && destination.payouts_enabled !== true) {
      logConnectSellerNotReady(parsed.seller.id, destination);
      return jsonError("seller_not_ready_to_charge", 409);
    }
  }

  const origin = configuredPublicOrigin();
  const applicationFeeCents = destination ? directChargeApplicationFeeCents(amount) : null;
  // Last guard of the minimum price (#546), on every charge that carries a
  // commission: the store must net something and the charge must clear the
  // Stripe minimum. The producer routes already refuse such a price; this one
  // also catches an offer priced through the internal API, which does not
  // check the minimum. Refuse before creating an order.
  const sellerNet = applicationFeeCents === null ? null : checkSellerNet({ amountMinor: amount, currency: "BRL" });
  if (sellerNet !== null && !sellerNet.ok) {
    return jsonError("product_price_below_platform_fee", 409);
  }
  if (provider === "financial_checkout") {
    // #154, fatia 1: para a loja do piloto, o pedido é gravado com o que foi
    // calculado acima, antes de qualquer cobrança. Fora do piloto, nada muda.
    let pedido: PedidoGravado | null = null;
    if (pedidoPinadoLiberado(parsed.seller.id)) {
      try {
        pedido = await gravarPedidoDoCheckout({
          sellerId: parsed.seller.id,
          productId: product.id,
          publicationId: product.publication_id,
          unitAmountMinor: amount,
          currency: "BRL",
          billingPeriod: "one_time",
          platformFeeMinor: applicationFeeCents,
          sellerNetMinor: sellerNet?.ok ? sellerNet.netMinor : null,
          planKey: DEFAULT_CHECKOUT_PLAN,
          buyerIdentitySub
        });
      } catch (error) {
        if (error instanceof DomainApiError && error.status === 409 && error.code === "price_changed") {
          return jsonError("price_changed", 409);
        }
        return jsonError("checkout_order_unavailable", 503);
      }
    }
    try {
      const endpointKey = checkoutWebhookEndpointKey();
      const result = await createFinancialExternalCheckoutIntent({
        operatorId: parsed.seller.financial_operator_id!,
        productCatalogItemId: parsed.seller.financial_product_catalog_item_id!,
        seller: {
          id: parsed.seller.id,
          slug: parsed.seller.slug,
          name: parsed.seller.name
        },
        buyer: {
          userId: buyerIdentitySub,
          email: input.buyerEmail,
          identitySub: buyerIdentitySub
        },
        product: {
          externalProductId: product.id,
          externalOfferId: product.publication_id,
          fulfillmentExternalProductId: mapping?.financialProductKey ?? product.id,
          title: product.title,
          description: product.description || null,
          imageUrl: product.cover
            ? `${origin}/api/storefronts/${encodeURIComponent(parsed.seller.slug)}/products/${encodeURIComponent(product.slug)}/cover`
            : null
        },
        pricing: {
          amountMinor: amount,
          currency: "BRL",
          billingPeriod: "one_time"
        },
        connect: {
          accountId: destination?.provider_account_id ?? null,
          applicationFeeAmountMinor: applicationFeeCents
        },
        successUrl: `${origin}/library?checkout=success`,
        cancelUrl: `${origin}/s/${input.sellerSlug}/p/${input.productSlug}?checkout=cancelled`,
        fulfillmentWebhookUrl: `${origin}/api/webhooks/checkouts/${endpointKey}`,
        fulfillmentEndpointKey: endpointKey,
        idempotencyKey: connectSeller && destination && applicationFeeCents !== null
          ? connectCheckoutIdempotencyKey({
            sellerId: parsed.seller.id,
            productId: product.id,
            offerId: product.publication_id,
            buyerIdentitySub,
            buyerEmail: input.buyerEmail,
            amountMinor: amount,
            accountId: destination.provider_account_id,
            applicationFeeAmountMinor: applicationFeeCents
          })
          : crypto.randomUUID()
      });
      if (pedido) await ligarAoIntentSemLancar(parsed.seller.id, pedido.orderId, result.checkoutIntentId);
      const stripeCheckoutUrl = await createFinancialExternalCheckoutSession(result.checkoutUrl);
      agendarAtribuicao(request, parsed.seller.id, result.checkoutIntentId);
      return NextResponse.json({ url: stripeCheckoutUrl }, { headers: noStore() });
    } catch (error) {
      if (error instanceof FinancialExternalCheckoutConfigError) return jsonError("financial_checkout_not_configured", 503);
      if (error instanceof FinancialExternalCheckoutError) {
        return jsonError(
          error.status >= 400 && error.status < 500 ? "financial_checkout_rejected" : "financial_checkout_unavailable",
          error.status >= 400 && error.status < 500 ? 409 : 503
        );
      }
      return jsonError("financial_checkout_unavailable", 503);
    }
  }

  let sessionUrl: string | null;
  try {
    const orderId = crypto.randomUUID();
    // Commission computed server-side from the pricing contract; never from
    // the request. Only meaningful on a direct charge — the legacy platform
    // branch has no connected account to split against.
    const session = await stripeClient().checkout.sessions.create({
      mode: "payment",
      // Pinned, NOT inherited from the Dashboard payment-method configuration.
      // Card settles synchronously: `checkout.session.completed` already carries
      // payment_status "paid", which is what the paired webhook requires before
      // granting access. Delayed-notification methods (boleto, Pix, …) confirm
      // hours-to-days later via `checkout.session.async_payment_succeeded`, so
      // enabling one in the Dashboard while this list is absent would take the
      // buyer's money and never grant the course. Adding a method here is only
      // safe together with the async event handling in
      // app/api/webhooks/stripe/route.ts (PAID_EVENT_TYPES).
      payment_method_types: ["card"],
      // Short on purpose (#148). The webhook checks the amount against the
      // offer's price at PAYMENT time, so a session left open while the seller
      // changes the price would charge the buyer and then grant nothing. Stripe
      // defaults to 24 h; this keeps the window near its 30-minute minimum.
      expires_at: Math.floor(Date.now() / 1_000) + CHECKOUT_SESSION_TTL_SECONDS,
      line_items: [
        {
          quantity: 1,
          price_data: {
            currency: STRIPE_CHECKOUT_CURRENCY,
            unit_amount: amount,
            product_data: { name: product.title }
          }
        }
      ],
      customer_email: input.buyerEmail,
      success_url: `${origin}/library?checkout=success`,
      cancel_url: `${origin}/s/${input.sellerSlug}/p/${input.productSlug}?checkout=cancelled`,
      metadata: {
        // external_product_id for the self-emitted webhook.
        financial_product_key: mapping!.financialProductKey,
        order_id: orderId,
        buyer_email: input.buyerEmail,
        // The buyer's authenticated subject, resolved server-side — the browser
        // never supplies it. Always non-empty here, since an unauthenticated
        // request never reaches this point; the webhook still treats "" and an
        // absent field as anonymous, for sessions created before this field.
        buyer_identity_sub: buyerIdentitySub
      },
      ...(applicationFeeCents === null
        ? {}
        : { payment_intent_data: { application_fee_amount: applicationFeeCents } })
    },
    // The Stripe-Account header is what makes this a DIRECT charge: the session
    // lives on the producer's connected account, the money settles there, and
    // the producer is the merchant of record — which is what makes "quem vendeu
    // paga o chargeback" real instead of a policy hope.
    destination ? { stripeAccount: destination.provider_account_id } : undefined);
    sessionUrl = session.url;
  } catch (error) {
    if (error instanceof StripeConfigError) return jsonError("stripe_not_configured", 503);
    return jsonError("stripe_session_failed", 502);
  }

  if (!sessionUrl) return jsonError("stripe_session_failed", 502);
  return NextResponse.json({ url: sessionUrl }, { headers: noStore() });
}

// Only a real, positive, one-time BRL price is chargeable in slice 1. A null
// price (unpriced/legacy offer) or a subscription period is rejected — never
// silently treated as free or as a recurring charge.
function purchasableOneTimeAmount(product: PublicStorefrontProduct): number | null {
  const price = product.price;
  if (!price) return null;
  if (price.billing_period !== "one_time") return null;
  if (price.currency !== "BRL") return null;
  if (!Number.isInteger(price.unit_amount_minor) || price.unit_amount_minor <= 0) return null;
  return price.unit_amount_minor;
}

function parseCreateSessionInput(rawBody: Buffer): CreateSessionInput | null {
  if (rawBody.length === 0) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody.toString("utf8"));
  } catch {
    return null;
  }
  if (!isRecord(parsed) || !hasExactKeys(parsed, ["seller_slug", "product_slug", "buyer_email"])) return null;
  const sellerSlug = parsed.seller_slug;
  const productSlug = parsed.product_slug;
  const buyerEmailRaw = parsed.buyer_email;
  if (typeof sellerSlug !== "string" || !SLUG.test(sellerSlug)) return null;
  if (typeof productSlug !== "string" || !SLUG.test(productSlug)) return null;
  if (typeof buyerEmailRaw !== "string") return null;
  // Enforce the DOMAIN INGRESS contract here, before any charge exists: the
  // webhook can only grant access if webhook_adapters.py accepts the e-mail, so
  // an address that is valid-looking but too long/short or non-ASCII must be
  // rejected at checkout instead of producing a paid, unfulfillable order.
  const buyerEmail = normalizeBuyerEmail(buyerEmailRaw);
  if (!isChargeableBuyerEmail(buyerEmail)) return null;
  return { sellerSlug, productSlug, buyerEmail };
}

function hasExactKeys<T extends string>(value: Record<string, unknown>, expected: readonly T[]): value is Record<T, unknown> {
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// #563: a atribuição da campanha roda depois da resposta (`after`), então o
// comprador não espera por ela. Nem o agendamento, nem a chamada, nem o log
// podem lançar daqui: depois da sessão criada, o checkout não vira 503.
function agendarAtribuicao(request: NextRequest, sellerId: string, checkoutIntentId: string): void {
  try {
    after(async () => {
      try {
        await registrarAtribuicaoDoCheckout(request, sellerId, checkoutIntentId);
      } catch (error) {
        logarSemLancar(error);
      }
    });
  } catch (error) {
    logarSemLancar(error);
  }
}

function logarSemLancar(error: unknown): void {
  try {
    atribuicaoFalhou(error);
  } catch {
    // O log é o último recurso. Se até ele falhar, a resposta segue igual.
  }
}

// #154: o intent já existe e o comprador segue para o pagamento. Se a ligação
// falhar, só o log fica sabendo: a loja, o pedido e o motivo, nunca o intent
// nem o comprador. A fatia 3 é quem passa a depender da ligação.
async function ligarAoIntentSemLancar(sellerId: string, orderId: string, checkoutIntentId: string): Promise<void> {
  try {
    await ligarPedidoAoIntent(sellerId, orderId, checkoutIntentId);
  } catch (error) {
    try {
      console.error(
        JSON.stringify({
          level: "error",
          marker: "appsell.checkout_order.attach_failed",
          seller_id: sellerId,
          order_id: orderId,
          reason: error instanceof DomainApiError ? error.code : "unexpected"
        })
      );
    } catch {
      // Sem log, o checkout segue igual.
    }
  }
}

function noStore() {
  return { "cache-control": "private, no-store" };
}

function jsonError(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}
