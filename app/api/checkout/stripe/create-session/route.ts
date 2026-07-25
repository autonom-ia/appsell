import { NextRequest, NextResponse } from "next/server";

import { isChargeableBuyerEmail, normalizeBuyerEmail } from "@/lib/buyer-email";
import { resolveCheckoutProduct } from "@/lib/checkout-config";
import { DomainApiError, readPublicStorefront } from "@/lib/internal-api-client.server";
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

  // Product must be provisioned to a Financial product key (interim map, Refs
  // #96); otherwise it is not sellable and we must not create a charge.
  const mapping = resolveCheckoutProduct(input.sellerSlug, input.productSlug);
  if (!mapping) return jsonError("product_not_configured", 409);

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

  let sessionUrl: string | null;
  try {
    const origin = configuredPublicOrigin();
    const orderId = crypto.randomUUID();
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
      // app/api/webhooks/stripe/route.ts (ASYNC_PAID_EVENT_TYPES).
      payment_method_types: ["card"],
      line_items: [
        {
          quantity: 1,
          price_data: {
            currency: "brl",
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
        financial_product_key: mapping.financialProductKey,
        order_id: orderId,
        buyer_email: input.buyerEmail
      }
    });
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
