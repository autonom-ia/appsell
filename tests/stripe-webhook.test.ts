import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// Hoisted so the (hoisted) vi.mock factories below can reference them.
const { constructEvent, proxyCheckoutWebhook, FakeStripeConfigError } = vi.hoisted(() => {
  class FakeStripeConfigError extends Error {}
  return { constructEvent: vi.fn(), proxyCheckoutWebhook: vi.fn(), FakeStripeConfigError };
});

// Fake Stripe: constructEvent is the signature gate. Tests drive it to either
// throw (bad signature) or return a crafted event.
vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: FakeStripeConfigError,
  stripeClient: () => ({ webhooks: { constructEvent } }),
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

function completedEvent(overrides: { paymentStatus?: string; metadata?: Record<string, string> | null } = {}) {
  return {
    id: "evt_test_1",
    type: "checkout.session.completed",
    created: EVENT_CREATED,
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

  it("acknowledges unrelated event types with 200 and does not emit", async () => {
    constructEvent.mockReturnValue({ id: "evt_2", type: "payment_intent.created", created: EVENT_CREATED, data: { object: {} } });
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

  it("treats a 409 from the proxy as an idempotent replay (200)", async () => {
    constructEvent.mockReturnValue(completedEvent());
    proxyCheckoutWebhook.mockResolvedValue({ status: 409, payload: { error: "conflict" } });
    const response = await POST(request("{}"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "replayed" });
  });

  it("returns 400 when required metadata is missing", async () => {
    constructEvent.mockReturnValue(completedEvent({ metadata: { order_id: "order-123" } }));
    const response = await POST(request("{}"));
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid_session_metadata" });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });
});
