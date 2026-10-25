import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  activateSellerFinancial,
  readSellerFinancialActivation,
  getSession,
  verifyMutation,
  csrfTokenForSession,
  DomainApiError
} = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    activateSellerFinancial: vi.fn(),
    readSellerFinancialActivation: vi.fn(),
    getSession: vi.fn(),
    verifyMutation: vi.fn(() => true),
    csrfTokenForSession: vi.fn(() => "csrf-activate"),
    DomainApiError
  };
});

vi.mock("@/lib/cookies", () => ({ sessionCookieName: () => "sid" }));
vi.mock("@/lib/internal-api-client.server", () => ({
  activateSellerFinancial,
  readSellerFinancialActivation,
  DomainApiError
}));
vi.mock("@/lib/oauth-service.server", () => ({ oauthService: () => ({ getSession }) }));
vi.mock("@/lib/redis-oauth-service", () => ({ isOAuthStoreUnavailable: () => false }));
vi.mock("@/lib/mutation-protection.server", () => ({ csrfTokenForSession, verifyMutation }));

import { GET, POST } from "../app/api/financial/seller-activation/[sellerId]/route";
import {
  ACTIVATE_SELLER_FINANCIAL_CSRF_ACTION,
  isCanonicalUuid
} from "../lib/financial-activation";

const ORIGIN = "http://localhost:3000";
const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const URL = `${ORIGIN}/api/financial/seller-activation/${SELLER_ID}`;
const SESSION = { subject: "user-1", identity: { issuer: "iss" } };
const context = (sellerId = SELLER_ID) => ({ params: Promise.resolve({ sellerId }) });

function getRequest(cookie = true) {
  return new NextRequest(URL, { headers: cookie ? { cookie: "sid=sess-1" } : {} });
}

function postRequest({
  fields = { _csrf: "csrf-activate" } as Record<string, string>,
  origin = ORIGIN as string | null,
  cookie = true
} = {}) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.set(key, value);
  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = "sid=sess-1";
  if (origin !== null) headers.origin = origin;
  return new NextRequest(URL, { method: "POST", headers, body: form });
}

const originalEnvironment = { ...process.env };

describe("financial seller activation route", () => {
  beforeEach(() => {
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    getSession.mockReset();
    getSession.mockResolvedValue(SESSION);
    verifyMutation.mockReset();
    verifyMutation.mockReturnValue(true);
    activateSellerFinancial.mockReset();
    readSellerFinancialActivation.mockReset();
    csrfTokenForSession.mockClear();
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("GET outside the flag returns no CSRF token", async () => {
    readSellerFinancialActivation.mockResolvedValue({ self_service: false });
    const response = await GET(getRequest(), context());
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ activation: { self_service: false }, csrf: null });
    expect(csrfTokenForSession).not.toHaveBeenCalled();
  });

  it("GET inside the flag returns the status and the activation token", async () => {
    readSellerFinancialActivation.mockResolvedValue({
      self_service: true,
      status: "available",
      reason: null
    });
    const response = await GET(getRequest(), context());
    expect(await response.json()).toEqual({
      activation: { self_service: true, status: "available", reason: null },
      csrf: { activate: "csrf-activate" }
    });
    expect(csrfTokenForSession).toHaveBeenCalledWith(
      "sess-1",
      ACTIVATE_SELLER_FINANCIAL_CSRF_ACTION
    );
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it("GET without a session is 401 and never reaches the API", async () => {
    const response = await GET(getRequest(false), context());
    expect(response.status).toBe(401);
    expect(readSellerFinancialActivation).not.toHaveBeenCalled();
  });

  it("POST activates with the CSRF token only and sends no business data", async () => {
    activateSellerFinancial.mockResolvedValue({ status: "active", changed: true });
    const response = await POST(postRequest(), context());
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "active", changed: true });
    expect(verifyMutation).toHaveBeenCalledWith(
      expect.anything(),
      "sess-1",
      ACTIVATE_SELLER_FINANCIAL_CSRF_ACTION,
      "csrf-activate"
    );
    expect(activateSellerFinancial).toHaveBeenCalledWith("sess-1", SESSION, SELLER_ID);
  });

  it("POST with any extra field is refused before the API", async () => {
    const response = await POST(
      postRequest({ fields: { _csrf: "csrf-activate", operator_id: "x" } }),
      context()
    );
    expect(response.status).toBe(422);
    expect(activateSellerFinancial).not.toHaveBeenCalled();
  });

  it("POST with a bad CSRF token is 403", async () => {
    verifyMutation.mockReturnValue(false);
    const response = await POST(postRequest(), context());
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "invalid_csrf" });
    expect(activateSellerFinancial).not.toHaveBeenCalled();
  });

  it("POST from another origin is 403", async () => {
    const response = await POST(postRequest({ origin: "https://evil.example" }), context());
    expect(response.status).toBe(403);
    expect(activateSellerFinancial).not.toHaveBeenCalled();
  });

  it("POST without a session cookie is 401", async () => {
    const response = await POST(postRequest({ cookie: false }), context());
    expect(response.status).toBe(401);
  });

  it("POST passes the API refusal through", async () => {
    activateSellerFinancial.mockRejectedValue(
      new DomainApiError(403, "financial_self_service_disabled")
    );
    const response = await POST(postRequest(), context());
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "financial_self_service_disabled" });
  });

  it("an unexpected failure is 503, not a crash", async () => {
    activateSellerFinancial.mockRejectedValue(new Error("boom"));
    const response = await POST(postRequest(), context());
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "financial_activation_unavailable" });
  });

  it("a path that is not a seller id is 404", async () => {
    const response = await GET(getRequest(), context("hub2you"));
    expect(response.status).toBe(404);
    expect(readSellerFinancialActivation).not.toHaveBeenCalled();
  });
});

describe("isCanonicalUuid", () => {
  it.each([
    [SELLER_ID, true],
    ["44444444-4444-4444-8444-44444444444", false],
    ["44444444-4444-4444-8444-4444444444444", false],
    ["44444444x4444-4444-8444-444444444444", false],
    ["4444444g-4444-4444-8444-444444444444", false],
    ["44444444-4444-4444-8444-44444444444A", false],
    ["", false]
  ])("%s -> %s", (value, expected) => {
    expect(isCanonicalUuid(value)).toBe(expected);
  });
});
