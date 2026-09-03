import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { readPublicStorefront } = vi.hoisted(() => ({ readPublicStorefront: vi.fn() }));
vi.mock("@/lib/internal-api-client.server", () => ({ readPublicStorefront }));

import { GET } from "../app/api/storefronts/[sellerSlug]/products/[productSlug]/banner/route";

const params = Promise.resolve({ sellerSlug: "hub2you", productSlug: "gerador-de-cotacoes" });
const banner = { url: "https://storage.test/banner?X-Amz-Signature=fresh", media_type: "image/jpeg", width: 1600, height: 500, expires_in_seconds: 60 };

function storefront(com = true) {
  return {
    schema_version: 1,
    seller: { id: "11111111-1111-4111-8111-111111111111", slug: "hub2you", name: "Hub2you" },
    products: [{
      id: "22222222-2222-4222-8222-222222222222",
      slug: "gerador-de-cotacoes",
      title: "Gerador de cotações",
      description: "Curso",
      publication_id: "33333333-3333-4333-8333-333333333333",
      publication_version: 1,
      price: null,
      ...(com ? { banner } : {})
    }]
  };
}

describe("GET banner público do produto", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    readPublicStorefront.mockResolvedValue(storefront());
  });

  it("serve o banner por um endereço estável, sem a assinatura do armazenamento", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array([9]), {
      headers: { "content-type": "image/jpeg", "content-length": "1" }
    })));

    const response = await GET(new Request("https://sell.test/banner"), { params });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("image/jpeg");
    expect(fetch).toHaveBeenCalledWith(banner.url, { cache: "no-store", redirect: "error" });
  });

  it("não confunde banner com capa: produto sem banner responde 404", async () => {
    readPublicStorefront.mockResolvedValue(storefront(false));
    const response = await GET(new Request("https://sell.test/banner"), { params });
    expect(response.status).toBe(404);
  });
});
