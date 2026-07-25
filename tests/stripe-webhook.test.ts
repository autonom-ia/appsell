import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// Hoisted so the (hoisted) vi.mock factories below can reference them.
const { constructEvent, proxyCheckoutWebhook, FakeStripeConfigError, keyMode } = vi.hoisted(() => {
  class FakeStripeConfigError extends Error {}
  return {
    constructEvent: vi.fn(),
    proxyCheckoutWebhook: vi.fn(),
    FakeStripeConfigError,
    // Mutable so a test can put the deployment in test mode and assert the
    // live/test fence.
    keyMode: { value: "live" as "live" | "test" }
  };
});

// Fake Stripe: constructEvent is the signature gate. Tests drive it to either
// throw (bad signature) or return a crafted event.
vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: FakeStripeConfigError,
  stripeClient: () => ({ webhooks: { constructEvent } }),
  stripeKeyMode: () => keyMode.value,
  stripeWebhookSecret: () => "whsec_test_123"
}));

// Fake proxy so nothing hits the domain ingress; we assert what it received.
vi.mock("@/lib/checkout-webhook-proxy.server", () => ({ proxyCheckoutWebhook }));

import { POST } from "../app/api/webhooks/stripe/route";

const ORIGIN = "http://localhost:3000";
const ENDPOINT_KEY = "endpoint_0123456789abcdefghijklmnopqrstuv";
const SECRET_B64 = Buffer.alloc(32, 7).toString("base64");
const EVENT_CREATED = 1_753_000_000;
const originalEnvironment = { ...process.env };

function request(rawBody: string, { signature = "t=1,v1=abc" }: { signature?: string | null } = {}): NextRequest {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (signature !== null) headers["stripe-signature"] = signature;
  return new Request(`${ORIGIN}/api/webhooks/stripe`, {
    method: "POST",
    headers,
    body: rawBody
  }) as unknown as NextRequest;
}

function completedEvent(
  overrides: {
    paymentStatus?: string;
    metadata?: Record<string, string> | null;
    type?: string;
    livemode?: boolean;
  } = {}
) {
  return {
    id: "evt_test_1",
    type: overrides.type ?? "checkout.session.completed",
    created: EVENT_CREATED,
    livemode: overrides.livemode ?? true,
    data: {
      object: {
        object: "checkout.session",
        payment_status: overrides.paymentStatus ?? "paid",
        metadata: overrides.metadata === undefined
          ? { financial_product_key: "prod_codex_v1", order_id: "order-123", buyer_email: "Buyer@Example.TEST" }
          : overrides.metadata
      }
    }
  };
}

describe("POST /api/webhooks/stripe", () => {
  beforeEach(() => {
    process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY = ENDPOINT_KEY;
    process.env.APPSELL_CHECKOUT_WEBHOOK_SECRET_B64 = SECRET_B64;
    keyMode.value = "live";
    constructEvent.mockReset();
    proxyCheckoutWebhook.mockReset();
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("rejects a bad signature with 400 and never emits", async () => {
    constructEvent.mockImplementation(() => {
      throw new Error("No signatures found matching the expected signature for payload");
    });
    const response = await POST(request("{}", { signature: "t=1,v1=wrong" }));
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid_signature" });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("returns 400 when the stripe-signature header is missing", async () => {
    const response = await POST(request("{}", { signature: null }));
    expect(response.status).toBe(400);
    expect(constructEvent).not.toHaveBeenCalled();
  });

  it("on a paid checkout.session.completed builds the v2 payload and calls the proxy", async () => {
    constructEvent.mockReturnValue(completedEvent());
    proxyCheckoutWebhook.mockResolvedValue({ status: 202, payload: { status: "accepted" } });

    const response = await POST(request(JSON.stringify(completedEvent())));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "accepted" });

    expect(proxyCheckoutWebhook).toHaveBeenCalledOnce();
    const [endpointKey, payloadBuffer, headers] = proxyCheckoutWebhook.mock.calls[0]!;
    expect(endpointKey).toBe(ENDPOINT_KEY);
    expect(headers.eventId).toBe("selfemit:order-123");
    expect(headers.signature).toMatch(/^v1=[0-9a-f]{64}$/);

    const payload = JSON.parse((payloadBuffer as Buffer).toString("utf8"));
    expect(payload).toEqual({
      event_type: "purchase.approved",
      external_product_id: "prod_codex_v1",
      external_purchase_id: "order-123",
      occurred_at: new Date(EVENT_CREATED * 1_000).toISOString(),
      buyer_email: "buyer@example.test",
      buyer_identity_sub: null
    });
  });

  it("grants on checkout.session.async_payment_succeeded (boleto/Pix settle later)", async () => {
    // Regression guard for the money hole: a delayed-notification method
    // completes the session unpaid and confirms with this event. Handling only
    // checkout.session.completed would keep the payment and never grant access.
    const event = completedEvent({ type: "checkout.session.async_payment_succeeded" });
    constructEvent.mockReturnValue(event);
    proxyCheckoutWebhook.mockResolvedValue({ status: 202, payload: { status: "accepted" } });

    const response = await POST(request(JSON.stringify(event)));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "accepted" });
    expect(proxyCheckoutWebhook).toHaveBeenCalledOnce();
    const [, payloadBuffer] = proxyCheckoutWebhook.mock.calls[0]!;
    expect(JSON.parse((payloadBuffer as Buffer).toString("utf8")).external_purchase_id).toBe("order-123");
  });

  it("ignores an async_payment_failed event and never emits", async () => {
    constructEvent.mockReturnValue(completedEvent({ type: "checkout.session.async_payment_failed" }));
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("refuses a test-mode event on a live deployment and never emits", async () => {
    // A test endpoint pointed at the live URL (or a Dashboard replay from the
    // test view) must not grant a real course for a fake payment.
    keyMode.value = "live";
    constructEvent.mockReturnValue(completedEvent({ livemode: false }));
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ received: true, ignored: "livemode_mismatch" });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("refuses a live-mode event on a test deployment and never emits", async () => {
    keyMode.value = "test";
    constructEvent.mockReturnValue(completedEvent({ livemode: true }));
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ received: true, ignored: "livemode_mismatch" });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("accepts a test-mode event on a test deployment", async () => {
    keyMode.value = "test";
    constructEvent.mockReturnValue(completedEvent({ livemode: false }));
    proxyCheckoutWebhook.mockResolvedValue({ status: 202, payload: { status: "accepted" } });
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(proxyCheckoutWebhook).toHaveBeenCalledOnce();
  });

  it("acknowledges unrelated event types with 200 and does not emit", async () => {
    constructEvent.mockReturnValue({ id: "evt_2", type: "payment_intent.created", created: EVENT_CREATED, livemode: true, data: { object: {} } });
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ received: true });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("acknowledges an unpaid completed session with 200 and does not emit", async () => {
    constructEvent.mockReturnValue(completedEvent({ paymentStatus: "unpaid" }));
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("reports a proxy 409 as a payload CONFLICT, never as a replay", async () => {
    // Migration 0020 returns 202 "replayed" for an identical payload and 409
    // only when the same event id carries a DIFFERENT payload hash — i.e. this
    // payload was rejected and never ingested. Calling that a replay claims the
    // purchase was fulfilled when this route cannot know that.
    constructEvent.mockReturnValue(completedEvent());
    proxyCheckoutWebhook.mockResolvedValue({ status: 409, payload: { error: "webhook_event_conflict" } });
    const response = await POST(request("{}"));
    // 2xx because a hash conflict is permanent — retrying delivers the same bytes.
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.conflict).toBe("event_id_payload_mismatch");
    expect(body.order_id).toBe("order-123");
    expect(body.status).toBeUndefined();
  });

  it("reports an identical-payload replay (202) as replayed", async () => {
    constructEvent.mockReturnValue(completedEvent());
    proxyCheckoutWebhook.mockResolvedValue({ status: 202, payload: { status: "replayed" } });
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "replayed" });
  });

  it("maps a proxy 404 to an integration fault, not a missing order", async () => {
    // The proxy never looks up an order: a 404 means the endpoint key is not
    // provisioned or the domain API lacks the route. Non-2xx on purpose so
    // Stripe keeps retrying while the configuration is fixed.
    constructEvent.mockReturnValue(completedEvent());
    proxyCheckoutWebhook.mockResolvedValue({ status: 404, payload: { error: "not_found" } });
    const response = await POST(request("{}"));
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "checkout_webhook_endpoint_not_found" });
  });

  it("acknowledges a session it did not create (no usable metadata) with 200", async () => {
    // Stripe retries EVERY non-2xx for ~3 days, so a 4xx here would only add
    // noise: a Dashboard Payment Link or another integration on the same
    // account will never gain our metadata on redelivery.
    constructEvent.mockReturnValue(completedEvent({ metadata: { order_id: "order-123" } }));
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ received: true, ignored: "unrecognized_session" });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("acknowledges with 200 when our own metadata fails the outbound contract", async () => {
    // buyer_email over the ingress' 242-octet bound: deterministic in the event
    // payload, so every redelivery fails identically.
    const tooLong = `${"a".repeat(240)}@example.test`;
    constructEvent.mockReturnValue(
      completedEvent({
        metadata: { financial_product_key: "prod_codex_v1", order_id: "order-123", buyer_email: tooLong }
      })
    );
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    // The order id is echoed so the lost sale is recoverable from the Stripe
    // event even though this repo has no logger in app/api (Refs #147).
    expect(await response.json()).toEqual({
      received: true,
      ignored: "payload_invalid",
      order_id: "order-123"
    });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("keeps 5xx for a transient ingress failure so Stripe retries", async () => {
    // The one case where retrying DOES help must stay non-2xx.
    constructEvent.mockReturnValue(completedEvent());
    proxyCheckoutWebhook.mockRejectedValue(new Error("ECONNREFUSED"));
    const response = await POST(request("{}"));
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "checkout_webhook_unavailable" });
  });
});
