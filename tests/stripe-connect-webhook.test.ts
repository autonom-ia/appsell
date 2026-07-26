import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const { constructEvent, syncPayoutAccountStatus, FakeStripeConfigError, keyMode, DomainApiError } =
  vi.hoisted(() => {
    class FakeStripeConfigError extends Error {}
    class DomainApiError extends Error {
      constructor(readonly status: number, readonly code: string) {
        super(code);
      }
    }
    return {
      constructEvent: vi.fn(),
      syncPayoutAccountStatus: vi.fn(),
      FakeStripeConfigError,
      DomainApiError,
      keyMode: vi.fn((): "live" | "test" => "live")
    };
  });

vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: FakeStripeConfigError,
  stripeClient: () => ({ webhooks: { constructEvent } }),
  stripeConnectWebhookSecret: () => "whsec_connect_test",
  stripeKeyMode: keyMode
}));

vi.mock("@/lib/internal-api-client.server", () => ({
  syncPayoutAccountStatus,
  DomainApiError
}));

const { proxyCheckoutWebhook } = vi.hoisted(() => ({
  proxyCheckoutWebhook: vi.fn()
}));

vi.mock("@/lib/checkout-webhook-proxy.server", () => ({
  proxyCheckoutWebhook
}));

vi.mock("@/lib/webhook-self-emit.server", () => ({
  SelfEmitPayloadError: class SelfEmitPayloadError extends Error {},
  buildCheckoutApprovedPayload: vi.fn((input: unknown) => input),
  selfEmitEventId: vi.fn(() => "selfemit:order-1"),
  selfEmitWebhookConfig: vi.fn(() => ({ endpointKey: "endpoint-key", secret: Buffer.alloc(32) })),
  serializeCheckoutPayload: vi.fn(() => Buffer.from("{}")),
  signSelfEmitWebhook: vi.fn(() => ({ "x-signature": "sig" }))
}));

import { POST } from "../app/api/webhooks/stripe/connect/route";

const ACCOUNT = "acct_1WebhookTest";

function accountUpdatedEvent(overrides: {
  livemode?: boolean;
  account?: unknown;
  objectId?: string;
  chargesEnabled?: boolean;
  payoutsEnabled?: boolean;
  detailsSubmitted?: boolean;
  disabledReason?: string | null;
  type?: string;
} = {}) {
  return {
    id: "evt_connect_1",
    type: overrides.type ?? "account.updated",
    livemode: overrides.livemode ?? true,
    account: "account" in overrides ? overrides.account : ACCOUNT,
    data: {
      object: {
        id: overrides.objectId ?? ACCOUNT,
        object: "account",
        charges_enabled: overrides.chargesEnabled ?? true,
        payouts_enabled: overrides.payoutsEnabled ?? true,
        details_submitted: overrides.detailsSubmitted ?? true,
        requirements: { disabled_reason: overrides.disabledReason ?? null }
      }
    }
  };
}

function request(body = "{}", signature: string | null = "sig_test"): NextRequest {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (signature !== null) headers["stripe-signature"] = signature;
  return new Request("http://localhost:3000/api/webhooks/stripe/connect", {
    method: "POST",
    headers,
    body
  }) as unknown as NextRequest;
}

describe("POST /api/webhooks/stripe/connect", () => {
  beforeEach(() => {
    constructEvent.mockReset();
    syncPayoutAccountStatus.mockReset();
    syncPayoutAccountStatus.mockResolvedValue({ updated: true, payout_account_id: "id" });
    keyMode.mockReturnValue("live");
    proxyCheckoutWebhook.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("refuses a missing signature before verification", async () => {
    const response = await POST(request("{}", null));
    expect(response.status).toBe(400);
    expect(constructEvent).not.toHaveBeenCalled();
  });

  it("refuses an invalid signature", async () => {
    constructEvent.mockImplementation(() => {
      throw new Error("bad signature");
    });
    const response = await POST(request());
    expect(response.status).toBe(400);
    expect(syncPayoutAccountStatus).not.toHaveBeenCalled();
  });

  it("forwards the verified status one-to-one to the sync call", async () => {
    constructEvent.mockReturnValue(
      accountUpdatedEvent({
        chargesEnabled: true,
        payoutsEnabled: false,
        detailsSubmitted: true,
        disabledReason: "requirements.pending_verification"
      })
    );
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(syncPayoutAccountStatus).toHaveBeenCalledWith({
      provider: "stripe",
      providerAccountId: ACCOUNT,
      chargesEnabled: true,
      payoutsEnabled: false,
      detailsSubmitted: true,
      disabledReason: "requirements.pending_verification"
    });
  });

  it("acknowledges other event types without syncing", async () => {
    constructEvent.mockReturnValue(accountUpdatedEvent({ type: "account.application.deauthorized" }));
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(syncPayoutAccountStatus).not.toHaveBeenCalled();
  });

  it("drops an event whose top-level account disagrees with the object", async () => {
    constructEvent.mockReturnValue(accountUpdatedEvent({ account: "acct_1SomeoneElse" }));
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect((await response.json()).ignored).toBe("account_mismatch");
    expect(syncPayoutAccountStatus).not.toHaveBeenCalled();
  });

  it("keeps a LIVE event retrying on a test-keyed deployment", async () => {
    keyMode.mockReturnValue("test");
    constructEvent.mockReturnValue(accountUpdatedEvent({ livemode: true }));
    const response = await POST(request());
    expect(response.status).toBe(503);
    expect(syncPayoutAccountStatus).not.toHaveBeenCalled();
  });

  it("drops a TEST event on a live deployment permanently", async () => {
    constructEvent.mockReturnValue(accountUpdatedEvent({ livemode: false }));
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect((await response.json()).ignored).toBe("livemode_mismatch");
    expect(syncPayoutAccountStatus).not.toHaveBeenCalled();
  });

  it("acknowledges an unknown account so Stripe stops redelivering", async () => {
    constructEvent.mockReturnValue(accountUpdatedEvent({}));
    syncPayoutAccountStatus.mockResolvedValue({ updated: false });
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect((await response.json()).ignored).toBe("unknown_account");
  });

  it("asks for a retry when the sync channel is down", async () => {
    constructEvent.mockReturnValue(accountUpdatedEvent({}));
    syncPayoutAccountStatus.mockRejectedValue(new DomainApiError(503, "domain_api_unavailable"));
    const response = await POST(request());
    expect(response.status).toBe(503);
  });

  it("bridges a paid direct-charge session into the signed self-emit", async () => {
    proxyCheckoutWebhook.mockResolvedValue({ status: 202, payload: { status: "accepted" } });
    constructEvent.mockReturnValue({
      id: "evt_paid_1",
      type: "checkout.session.completed",
      livemode: true,
      account: ACCOUNT,
      created: 1_753_000_000,
      data: {
        object: {
          id: "cs_live_1",
          object: "checkout.session",
          payment_status: "paid",
          metadata: {
            financial_product_key: "prod_curso_real_v1",
            order_id: "order-1",
            buyer_email: "buyer@example.test",
            buyer_identity_sub: "sub-1"
          }
        }
      }
    });
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect((await response.json()).status).toBe("accepted");
    expect(proxyCheckoutWebhook).toHaveBeenCalledTimes(1);
    expect(syncPayoutAccountStatus).not.toHaveBeenCalled();
  });

  it("acknowledges an unpaid session without emitting", async () => {
    constructEvent.mockReturnValue({
      id: "evt_unpaid_1",
      type: "checkout.session.completed",
      livemode: true,
      account: ACCOUNT,
      created: 1_753_000_000,
      data: {
        object: {
          id: "cs_live_2",
          object: "checkout.session",
          payment_status: "unpaid",
          metadata: {}
        }
      }
    });
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect((await response.json()).ignored).toBe("not_paid");
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("drops a paid session that create-session did not mint", async () => {
    constructEvent.mockReturnValue({
      id: "evt_foreign_1",
      type: "checkout.session.completed",
      livemode: true,
      account: ACCOUNT,
      created: 1_753_000_000,
      data: {
        object: {
          id: "cs_live_3",
          object: "checkout.session",
          payment_status: "paid",
          metadata: {}
        }
      }
    });
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect((await response.json()).ignored).toBe("unrecognized_session");
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("acknowledges a deterministic rejection instead of retrying forever", async () => {
    constructEvent.mockReturnValue(accountUpdatedEvent({}));
    syncPayoutAccountStatus.mockRejectedValue(new DomainApiError(422, "invalid_request_body"));
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect((await response.json()).ignored).toBe("sync_rejected_payload");
  });
});
