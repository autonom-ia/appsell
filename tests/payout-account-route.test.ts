import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  createConnectedAccount,
  createOnboardingAccountSession,
  deleteConnectedAccountBestEffort,
  readSellerPayoutAccount,
  bindSellerPayoutAccount,
  getSession,
  verifyMutation,
  DomainApiError,
  FakeStripeConfigError,
  stripePublishableKey
} = vi.hoisted(() => {
  class FakeStripeConfigError extends Error {}
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    createConnectedAccount: vi.fn(),
    createOnboardingAccountSession: vi.fn(async () => "accs_secret_test"),
    deleteConnectedAccountBestEffort: vi.fn(async () => true),
    readSellerPayoutAccount: vi.fn(),
    bindSellerPayoutAccount: vi.fn(),
    getSession: vi.fn(),
    verifyMutation: vi.fn(() => true),
    DomainApiError,
    FakeStripeConfigError,
    stripePublishableKey: vi.fn(() => "pk_live_routetestplaceholder")
  };
});

vi.mock("@/lib/stripe-connect.server", () => ({
  createConnectedAccount,
  createOnboardingAccountSession,
  deleteConnectedAccountBestEffort
}));

vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: FakeStripeConfigError,
  stripePublishableKey
}));

vi.mock("@/lib/internal-api-client.server", () => ({
  readSellerPayoutAccount,
  bindSellerPayoutAccount,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

vi.mock("@/lib/redis-oauth-service", () => ({
  isOAuthStoreUnavailable: () => false
}));

vi.mock("@/lib/mutation-protection.server", () => ({
  CONNECT_PAYOUT_ACCOUNT_CSRF_ACTION: "POST:/api/sellers/payout-account",
  csrfTokenForSession: () => "csrf-token-test",
  verifyMutation
}));

import { GET, POST } from "../app/api/sellers/[sellerId]/payout-account/route";

const ORIGIN = "http://localhost:3000";
const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const FRESH_ACCOUNT = "acct_1FreshTest";
const BOUND_ACCOUNT = "acct_1BoundTest";

function boundProjection(providerAccountId = BOUND_ACCOUNT) {
  return {
    payout_account_id: "55555555-5555-4555-8555-555555555555",
    provider: "stripe",
    provider_account_id: providerAccountId,
    charges_enabled: false,
    payouts_enabled: false,
    details_submitted: false,
    disabled_reason: null,
    status_synced_at: "2026-07-25T12:00:00+00:00"
  };
}

function getRequest({ cookie = true }: { cookie?: boolean } = {}) {
  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = "appsell_session=session-1";
  return new NextRequest(`${ORIGIN}/api/sellers/${SELLER_ID}/payout-account`, { headers });
}

function postRequest({
  cookie = true,
  origin = ORIGIN
}: { cookie?: boolean; origin?: string | null } = {}) {
  const form = new FormData();
  form.set("_csrf", "csrf-token-test");
  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = "appsell_session=session-1";
  if (origin !== null) headers.origin = origin;
  return new NextRequest(`${ORIGIN}/api/sellers/${SELLER_ID}/payout-account`, {
    method: "POST",
    headers,
    body: form
  });
}

const context = { params: Promise.resolve({ sellerId: SELLER_ID }) };
const originalEnvironment = { ...process.env };

describe("payout-account route", () => {
  beforeEach(() => {
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    getSession.mockReset();
    getSession.mockResolvedValue({ subject: "user-1", identity: { issuer: "iss" } });
    verifyMutation.mockReset();
    verifyMutation.mockReturnValue(true);
    readSellerPayoutAccount.mockReset();
    bindSellerPayoutAccount.mockReset();
    createConnectedAccount.mockReset();
    createOnboardingAccountSession.mockClear();
    createOnboardingAccountSession.mockResolvedValue("accs_secret_test");
    deleteConnectedAccountBestEffort.mockClear();
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("GET returns the binding, the connect CSRF token and the publishable key", async () => {
    // The publishable key travels in THIS response because a NEXT_PUBLIC_ var
    // would be inlined at build time, and the deployment builds the image
    // without it — the browser would get undefined and no producer could ever
    // start the onboarding.
    readSellerPayoutAccount.mockResolvedValue({ payout_account: boundProjection() });
    const response = await GET(getRequest(), context);
    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.payout_account.provider_account_id).toBe(BOUND_ACCOUNT);
    expect(payload.csrf.connect).toBe("csrf-token-test");
    expect(payload.publishable_key).toBe("pk_live_routetestplaceholder");
  });

  it("GET still reports the status when no publishable key is configured", async () => {
    // Reading the payout status must not depend on the connect step being
    // configured: the producer keeps seeing where they stand, and the card is
    // the one that says the connect action is unavailable.
    stripePublishableKey.mockImplementationOnce(() => {
      throw new FakeStripeConfigError("invalid_stripe_publishable_key");
    });
    readSellerPayoutAccount.mockResolvedValue({ payout_account: boundProjection() });
    const response = await GET(getRequest(), context);
    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.publishable_key).toBeNull();
    expect(payload.payout_account.provider_account_id).toBe(BOUND_ACCOUNT);
  });

  it("GET refuses without a session cookie", async () => {
    const response = await GET(getRequest({ cookie: false }), context);
    expect(response.status).toBe(401);
    expect(readSellerPayoutAccount).not.toHaveBeenCalled();
  });

  it("POST first connect binds the id FROM the accounts.create response", async () => {
    // The route must never take an account id from the request: the only
    // account it may bind is the one the authenticated provider call returned.
    readSellerPayoutAccount.mockResolvedValue({ payout_account: null });
    createConnectedAccount.mockResolvedValue({
      id: FRESH_ACCOUNT,
      charges_enabled: false,
      payouts_enabled: false,
      details_submitted: false
    });
    bindSellerPayoutAccount.mockResolvedValue({
      payout_account_id: "66666666-6666-4666-8666-666666666666"
    });
    const response = await POST(postRequest(), context);
    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.client_secret).toBe("accs_secret_test");
    expect(payload.payout_account.provider_account_id).toBe(FRESH_ACCOUNT);
    expect(bindSellerPayoutAccount).toHaveBeenCalledWith(
      "session-1",
      expect.anything(),
      SELLER_ID,
      expect.objectContaining({ provider: "stripe", providerAccountId: FRESH_ACCOUNT })
    );
    expect(createOnboardingAccountSession).toHaveBeenCalledWith(FRESH_ACCOUNT);
    expect(deleteConnectedAccountBestEffort).not.toHaveBeenCalled();
  });

  it("POST resumes an existing binding without creating a new provider account", async () => {
    readSellerPayoutAccount.mockResolvedValue({ payout_account: boundProjection() });
    const response = await POST(postRequest(), context);
    expect(response.status).toBe(200);
    expect(createConnectedAccount).not.toHaveBeenCalled();
    expect(bindSellerPayoutAccount).not.toHaveBeenCalled();
    expect(createOnboardingAccountSession).toHaveBeenCalledWith(BOUND_ACCOUNT);
  });

  it("POST losing the bind race deletes the fresh account and serves the winner", async () => {
    readSellerPayoutAccount
      .mockResolvedValueOnce({ payout_account: null })
      .mockResolvedValueOnce({ payout_account: boundProjection() });
    createConnectedAccount.mockResolvedValue({
      id: FRESH_ACCOUNT,
      charges_enabled: false,
      payouts_enabled: false,
      details_submitted: false
    });
    bindSellerPayoutAccount.mockRejectedValue(
      new DomainApiError(409, "payout_account_conflict")
    );
    const response = await POST(postRequest(), context);
    expect(response.status).toBe(200);
    expect(deleteConnectedAccountBestEffort).toHaveBeenCalledWith(FRESH_ACCOUNT);
    expect(createOnboardingAccountSession).toHaveBeenCalledWith(BOUND_ACCOUNT);
  });

  it("POST cleans up the fresh account when the bind fails outright", async () => {
    readSellerPayoutAccount.mockResolvedValue({ payout_account: null });
    createConnectedAccount.mockResolvedValue({
      id: FRESH_ACCOUNT,
      charges_enabled: false,
      payouts_enabled: false,
      details_submitted: false
    });
    bindSellerPayoutAccount.mockRejectedValue(new DomainApiError(403, "forbidden"));
    const response = await POST(postRequest(), context);
    expect(response.status).toBe(403);
    expect(deleteConnectedAccountBestEffort).toHaveBeenCalledWith(FRESH_ACCOUNT);
    expect(createOnboardingAccountSession).not.toHaveBeenCalled();
  });

  it("POST refuses an invalid CSRF token before touching the provider", async () => {
    verifyMutation.mockReturnValue(false);
    const response = await POST(postRequest(), context);
    expect(response.status).toBe(403);
    expect(createConnectedAccount).not.toHaveBeenCalled();
    expect(readSellerPayoutAccount).not.toHaveBeenCalled();
  });

  it("POST refuses a cross-origin request", async () => {
    const response = await POST(postRequest({ origin: "https://evil.test" }), context);
    expect(response.status).toBe(403);
    expect(createConnectedAccount).not.toHaveBeenCalled();
  });

  it("POST refuses without a session cookie", async () => {
    const response = await POST(postRequest({ cookie: false }), context);
    expect(response.status).toBe(401);
    expect(createConnectedAccount).not.toHaveBeenCalled();
  });
});
