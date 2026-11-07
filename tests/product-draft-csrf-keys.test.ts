import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// O GET do produto ganhou dois tokens para a segunda oferta (#553, fase 2b).
// Este teste prende as chaves que o editor já usa: nenhuma muda de nome nem
// de ação, e as novas só se somam.

vi.mock("server-only", () => ({}));

const { readDomainProductDraft, getSession, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    readDomainProductDraft: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    getSession: vi.fn(async () => ({ identity: { sub: "seller" } })),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  readDomainProductDraft,
  updateDomainProductCourseDraft: vi.fn(),
  updateDomainProductDraft: vi.fn(),
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

vi.mock("@/lib/redis-oauth-service", () => ({
  isOAuthStoreUnavailable: () => false
}));

vi.mock("@/lib/mutation-protection.server", async (importOriginal) => {
  const original = await importOriginal<Record<string, unknown>>();
  return {
    ...original,
    csrfTokenForSession: (_session: string, action: string) => `token:${action}`
  };
});

import { GET } from "../app/api/sellers/[sellerId]/products/[productId]/route";

const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const PRODUCT_ID = "55555555-5555-4555-8555-555555555555";

// O que o editor lia antes da #553, com a ação de cada token.
const CHAVES_ANTIGAS = {
  update: "PATCH:/api/products",
  publish: "POST:/api/products/publications",
  offer_status: "PATCH:/api/products/offers/status",
  access_link_create: "POST:/api/products/access-links",
  access_link_revoke: "POST:/api/products/access-links/revoke",
  access_link_rotate: "POST:/api/products/access-links/rotate",
  cover_intent: "POST:/api/products/cover/upload-intents",
  cover_confirm: "POST:/api/products/cover/uploads/confirm",
  cover_clear: "POST:/api/products/cover/clear",
  headline: "PATCH:/api/products/headline"
};

describe("tokens CSRF do GET do produto", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    readDomainProductDraft.mockResolvedValue({
      product: { id: PRODUCT_ID, offers: [], multi_offer_enabled: false }
    });
  });

  it("mantém as chaves antigas e soma só as duas da segunda oferta", async () => {
    const response = await GET(
      new NextRequest(`http://localhost:3000/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, {
        headers: { cookie: "appsell_session=session-1" }
      }),
      { params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID }) }
    );
    expect(response.status).toBe(200);
    const payload = await response.json() as { csrf: Record<string, string> };

    for (const [chave, acao] of Object.entries(CHAVES_ANTIGAS)) {
      expect(payload.csrf[chave]).toBe(`token:${acao}`);
    }
    expect(payload.csrf.offer_create).toBe("token:POST:/api/products/offers");
    expect(payload.csrf.offer_default).toBe("token:POST:/api/products/offers/default");
    const novas = Object.keys(payload.csrf).filter((chave) => !Object.hasOwn(CHAVES_ANTIGAS, chave));
    expect(novas.sort()).toEqual(["offer_create", "offer_default"]);
  });

  it("a flag vem da api e passa intacta, sem o BFF decidir", async () => {
    readDomainProductDraft.mockResolvedValueOnce({
      product: { id: PRODUCT_ID, offers: [{ id: "o1", is_default: true }], multi_offer_enabled: true }
    });
    const response = await GET(
      new NextRequest(`http://localhost:3000/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, {
        headers: { cookie: "appsell_session=session-1" }
      }),
      { params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID }) }
    );
    const payload = await response.json() as { product: Record<string, unknown> };
    expect(payload.product.multi_offer_enabled).toBe(true);
    expect(payload.product.offers).toEqual([{ id: "o1", is_default: true }]);
  });
});
