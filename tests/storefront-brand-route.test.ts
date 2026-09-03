import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { readPublicStorefront } = vi.hoisted(() => ({ readPublicStorefront: vi.fn() }));
vi.mock("@/lib/internal-api-client.server", () => ({ readPublicStorefront }));

import { GET } from "../app/api/storefronts/[sellerSlug]/brand/[kind]/route";

function storefront(marca: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    seller: { id: "11111111-1111-4111-8111-111111111111", slug: "hub2you", name: "Hub2You", ...marca },
    products: []
  };
}

const logo = { url: "https://storage.test/logo?X-Amz-Signature=fresh", media_type: "image/jpeg", width: 1081, height: 1081, expires_in_seconds: 60 };

function params(kind: string, sellerSlug = "hub2you") {
  return Promise.resolve({ sellerSlug, kind });
}

describe("GET marca pública da loja", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    readPublicStorefront.mockResolvedValue(storefront({ logo }));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array([1, 2, 3]), {
      headers: { "content-type": "image/jpeg", "content-length": "3" }
    })));
  });

  it("busca a URL assinada no servidor e serve os bytes por um endereço estável", async () => {
    const response = await GET(new Request("https://sell.test/brand"), { params: params("logo") });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("image/jpeg");
    expect(response.headers.get("cross-origin-resource-policy")).toBe("cross-origin");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(fetch).toHaveBeenCalledWith(logo.url, { cache: "no-store", redirect: "error", signal: expect.any(AbortSignal) });
  });

  it("recusa um tipo de imagem que não existe, sem consultar a vitrine", async () => {
    const response = await GET(new Request("https://sell.test/brand"), { params: params("capa") });
    expect(response.status).toBe(404);
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });

  it("responde 404 quando a loja não tem aquela imagem", async () => {
    const response = await GET(new Request("https://sell.test/brand"), { params: params("banner") });
    expect(response.status).toBe(404);
  });

  it("recusa endereço de loja fora do formato", async () => {
    const response = await GET(new Request("https://sell.test/brand"), { params: params("logo", "../etc") });
    expect(response.status).toBe(404);
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });

  it("falha fechada quando o armazenamento devolve outro tipo", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("não é imagem", { headers: { "content-type": "text/html" } })));
    const response = await GET(new Request("https://sell.test/brand"), { params: params("logo") });
    expect(response.status).toBe(503);
  });

  it("não segue redirecionamento do armazenamento", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("unexpected redirect"); }));
    const response = await GET(new Request("https://sell.test/brand"), { params: params("logo") });
    expect(response.status).toBe(503);
  });
});
