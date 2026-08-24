import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  createDomainAccessLink,
  listDomainAccessLinks,
  rotateDomainAccessLink,
  revokeDomainAccessLink,
  redeemDomainAccessLink,
  getSession,
  consumeRateLimit,
  verifyMutation,
  DomainApiError
} = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    createDomainAccessLink: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    listDomainAccessLinks: vi.fn(async () => ({ access_links: [] })),
    rotateDomainAccessLink: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    revokeDomainAccessLink: vi.fn(async () => ({ changed: true })),
    redeemDomainAccessLink: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    getSession: vi.fn(async () => ({ identity: { sub: "buyer" } })),
    consumeRateLimit: vi.fn(async () => true),
    verifyMutation: vi.fn(() => true),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  createDomainAccessLink,
  listDomainAccessLinks,
  rotateDomainAccessLink,
  revokeDomainAccessLink,
  redeemDomainAccessLink,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession, consumeRateLimit })
}));

vi.mock("@/lib/redis-oauth-service", () => ({
  isOAuthStoreUnavailable: () => false
}));

vi.mock("@/lib/mutation-protection.server", () => ({
  CREATE_ACCESS_LINK_CSRF_ACTION: "POST:/api/products/access-links",
  REVOKE_ACCESS_LINK_CSRF_ACTION: "POST:/api/products/access-links/revoke",
  ROTATE_ACCESS_LINK_CSRF_ACTION: "POST:/api/products/access-links/rotate",
  REDEEM_ACCESS_LINK_CSRF_ACTION: "POST:/api/library/access-links/redeem",
  csrfTokenForSession: () => "csrf-token-test",
  verifyMutation
}));

import { POST as createLink } from
  "../app/api/sellers/[sellerId]/products/[productId]/access-links/route";
import { POST as rotateLink } from
  "../app/api/sellers/[sellerId]/products/[productId]/access-links/[accessLinkId]/rotate/route";
import { POST as redeemLink } from "../app/api/library/access-links/redeem/route";
import { accessLinkDigest } from "../lib/access-link-token.server";

const ORIGIN = "http://localhost:3000";
const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const PRODUCT_ID = "55555555-5555-4555-8555-555555555555";
const LINK_ID = "66666666-6666-4666-8666-666666666666";
const IDEMPOTENCY = "77777777-7777-4777-8777-777777777777";
const TOKEN = "a".repeat(64);

const sellerContext = {
  params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID })
};
const linkContext = {
  params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID, accessLinkId: LINK_ID })
};
const originalEnvironment = { ...process.env };

function formRequest(path: string, entries: Record<string, string>, origin: string | null = ORIGIN) {
  const form = new FormData();
  for (const [key, value] of Object.entries(entries)) form.set(key, value);
  const headers: Record<string, string> = { cookie: "appsell_session=session-1" };
  if (origin !== null) headers.origin = origin;
  return new NextRequest(`${ORIGIN}${path}`, { method: "POST", headers, body: form });
}

describe("access link routes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createDomainAccessLink.mockResolvedValue({ access_link_id: "link-1" });
    rotateDomainAccessLink.mockResolvedValue({ access_link_id: "link-1" });
    redeemDomainAccessLink.mockResolvedValue({ created: true, seat_consumed: true });
    verifyMutation.mockReturnValue(true);
    getSession.mockResolvedValue({ identity: { sub: "buyer" } });
    consumeRateLimit.mockResolvedValue(true);
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("mints the token in the server and forwards only its digest", async () => {
    // The raw token must exist in exactly one place: the response to the seller
    // who just created the link. If it reached the domain API it would be logged
    // upstream and stored, and a dump would hand out access.
    const response = await createLink(
      formRequest(
        `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/access-links`,
        { _csrf: "csrf-token-test", idempotency_key: IDEMPOTENCY, label: "Turma A", max_uses: "30", expires_at: "" }
      ),
      sellerContext
    );

    expect(response.status).toBe(201);
    const payload = await response.json();
    const token = String(payload.url).split("/c/")[1];
    expect(token).toMatch(/^[0-9a-f]{64}$/);

    const forwarded = createDomainAccessLink.mock.calls[0]?.[4] as Record<string, unknown>;
    expect(forwarded.tokenSha256).toBe(accessLinkDigest(token));
    expect(forwarded.tokenPrefix).toBe(token.slice(0, 8));
    expect(JSON.stringify(createDomainAccessLink.mock.calls)).not.toContain(token);
    expect(forwarded.maxUses).toBe(30);
    expect(forwarded.expiresAt).toBeNull();
  });

  it("never reuses a token between two links", async () => {
    const first = await createLink(
      formRequest(`/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/access-links`,
        { _csrf: "c", idempotency_key: IDEMPOTENCY, label: "A", max_uses: "", expires_at: "" }),
      sellerContext
    );
    const second = await createLink(
      formRequest(`/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/access-links`,
        { _csrf: "c", idempotency_key: IDEMPOTENCY, label: "B", max_uses: "", expires_at: "" }),
      sellerContext
    );
    expect((await first.json()).url).not.toBe((await second.json()).url);
  });

  it.each([
    { label: " sobra espaco", max_uses: "30", expires_at: "", field: "label" },
    { label: "", max_uses: "30", expires_at: "", field: "label" },
    { label: "Turma", max_uses: "0", expires_at: "", field: "max_uses" },
    { label: "Turma", max_uses: "200000", expires_at: "", field: "max_uses" },
    { label: "Turma", max_uses: "trinta", expires_at: "", field: "max_uses" },
    { label: "Turma", max_uses: "30", expires_at: "ontem", field: "expires_at" },
    { label: "Turma", max_uses: "30", expires_at: "2020-01-01T00:00:00Z", field: "expires_at" }
  ])("rejects a malformed $field before minting anything", async (input) => {
    const response = await createLink(
      formRequest(`/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/access-links`, {
        _csrf: "csrf-token-test",
        idempotency_key: IDEMPOTENCY,
        label: input.label,
        max_uses: input.max_uses,
        expires_at: input.expires_at
      }),
      sellerContext
    );
    expect(response.status).toBe(422);
    expect(createDomainAccessLink).not.toHaveBeenCalled();
  });

  it("refuses creation from a foreign origin", async () => {
    const response = await createLink(
      formRequest(
        `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/access-links`,
        { _csrf: "csrf-token-test", idempotency_key: IDEMPOTENCY, label: "Turma", max_uses: "", expires_at: "" },
        "https://evil.test"
      ),
      sellerContext
    );
    expect(response.status).toBe(403);
    expect(createDomainAccessLink).not.toHaveBeenCalled();
  });

  it("refuses creation without a valid csrf token", async () => {
    verifyMutation.mockReturnValue(false);
    const response = await createLink(
      formRequest(`/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/access-links`,
        { _csrf: "wrong", idempotency_key: IDEMPOTENCY, label: "Turma", max_uses: "", expires_at: "" }),
      sellerContext
    );
    expect(response.status).toBe(403);
    expect(createDomainAccessLink).not.toHaveBeenCalled();
  });

  it("rotation issues a new address and forwards its digest", async () => {
    const response = await rotateLink(
      formRequest(
        `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/access-links/${LINK_ID}/rotate`,
        { _csrf: "csrf-token-test", idempotency_key: IDEMPOTENCY }
      ),
      linkContext
    );
    expect(response.status).toBe(200);
    const token = String((await response.json()).url).split("/c/")[1];
    const forwarded = rotateDomainAccessLink.mock.calls[0]?.[5] as Record<string, unknown>;
    expect(forwarded.tokenSha256).toBe(accessLinkDigest(token));
    expect(JSON.stringify(rotateDomainAccessLink.mock.calls)).not.toContain(token);
  });

  it("redemption forwards the digest and never the token", async () => {
    const response = await redeemLink(
      formRequest("/api/library/access-links/redeem", {
        _csrf: "csrf-token-test",
        idempotency_key: IDEMPOTENCY,
        token: TOKEN
      })
    );
    expect(response.status).toBe(201);
    const forwarded = redeemDomainAccessLink.mock.calls[0]?.[2] as Record<string, unknown>;
    expect(forwarded.tokenSha256).toBe(accessLinkDigest(TOKEN));
    expect(JSON.stringify(redeemDomainAccessLink.mock.calls)).not.toContain(TOKEN);
  });

  it("stops a redemption flood with 429 before it reaches the domain", async () => {
    // Without a ceiling this endpoint is an oracle: an authenticated account
    // could probe tokens at full request rate.
    consumeRateLimit.mockResolvedValue(false);
    const response = await redeemLink(
      formRequest("/api/library/access-links/redeem", {
        _csrf: "csrf-token-test",
        idempotency_key: IDEMPOTENCY,
        token: TOKEN
      })
    );
    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({ error: "rate_limited" });
    expect(redeemDomainAccessLink).not.toHaveBeenCalled();
  });

  it.each([
    "short",
    "A".repeat(64),
    "z".repeat(64),
    "a".repeat(63)
  ])("rejects a malformed token %s without calling the domain", async (token) => {
    const response = await redeemLink(
      formRequest("/api/library/access-links/redeem", {
        _csrf: "csrf-token-test",
        idempotency_key: IDEMPOTENCY,
        token
      })
    );
    expect(response.status).toBe(422);
    expect(redeemDomainAccessLink).not.toHaveBeenCalled();
  });

  it("writes nothing to the console that could leak a token", async () => {
    // A1.10: the token is a credential. If any layer logged it, a log shipper
    // would carry working access out of the system.
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) =>
      vi.spyOn(console, level).mockImplementation(() => {})
    );
    try {
      redeemDomainAccessLink.mockRejectedValueOnce(new DomainApiError(404, "access_link_unavailable"));
      await redeemLink(
        formRequest("/api/library/access-links/redeem", {
          _csrf: "csrf-token-test",
          idempotency_key: IDEMPOTENCY,
          token: TOKEN
        })
      );
      const written = spies.flatMap((spy) => spy.mock.calls.flat()).map(String).join(" ");
      expect(written).not.toContain(TOKEN);
    } finally {
      for (const spy of spies) spy.mockRestore();
    }
  });

  it("passes an unavailable link through as the domain reported it", async () => {
    redeemDomainAccessLink.mockRejectedValueOnce(new DomainApiError(404, "access_link_unavailable"));
    const response = await redeemLink(
      formRequest("/api/library/access-links/redeem", {
        _csrf: "csrf-token-test",
        idempotency_key: IDEMPOTENCY,
        token: TOKEN
      })
    );
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "access_link_unavailable" });
  });
});
