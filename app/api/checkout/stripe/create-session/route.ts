import { NextRequest, NextResponse } from "next/server";

import { isChargeableBuyerEmail, normalizeBuyerEmail } from "@/lib/buyer-email";
import { resolveBuyerIdentity } from "@/lib/buyer-identity.server";
import { STRIPE_CHECKOUT_CURRENCY, resolveCheckoutProduct } from "@/lib/checkout-config";
import { directChargeApplicationFeeCents } from "@/lib/checkout-fee";
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
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { parsePublicStorefront, type PublicStorefrontProduct } from "@/lib/public-storefront";
import { StripeConfigError, stripeClient } from "@/lib/stripe-config.server";

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

  const provider = checkoutProvider();
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
  // destination below belongs only to the legacy local direct-charge path.
  let destination: { provider_account_id: string; charges_enabled: boolean } | null = null;
  if (provider !== "financial_checkout") {
    try {
      destination = (await resolveCheckoutPayoutDestination(parsed.seller.id)).payout_destination;
    } catch {
      return jsonError("payout_destination_unavailable", 503);
    }
    if (destination && !destination.charges_enabled) {
      return jsonError("seller_not_ready_to_charge", 409);
    }
  }

  const origin = configuredPublicOrigin();
  const applicationFeeCents = destination ? directChargeApplicationFeeCents(amount) : null;
  if (provider === "financial_checkout") {
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
        idempotencyKey: crypto.randomUUID()
      });
      const stripeCheckoutUrl = await createFinancialExternalCheckoutSession(result.checkoutUrl);
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

function noStore() {
  return { "cache-control": "private, no-store" };
}

function jsonError(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}
