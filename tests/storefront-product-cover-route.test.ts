import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { readPublicStorefront } = vi.hoisted(() => ({ readPublicStorefront: vi.fn() }));
vi.mock("@/lib/internal-api-client.server", () => ({ readPublicStorefront }));

import { GET } from "../app/api/storefronts/[sellerSlug]/products/[productSlug]/cover/route";

const params = Promise.resolve({ sellerSlug: "hub2you", productSlug: "ia-na-pratica" });

function storefront(cover = true) {
  return {
    schema_version: 1,
    seller: { id: "11111111-1111-4111-8111-111111111111", slug: "hub2you", name: "Hub2You" },
    products: [{
      id: "22222222-2222-4222-8222-222222222222",
      slug: "ia-na-pratica",
      title: "IA na prática",
      description: "Curso",
      publication_id: "33333333-3333-4333-8333-333333333333",
      publication_version: 1,
      price: { unit_amount_minor: 4700, currency: "BRL", billing_period: "one_time" },
      ...(cover ? { cover: {
        url: "https://storage.test/cover?X-Amz-Signature=fresh",
        media_type: "image/png",
        width: 1672,
        height: 941,
        expires_in_seconds: 60
      } } : {})
    }]
  };
}

describe("GET public storefront product cover", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    readPublicStorefront.mockResolvedValue(storefront());
  });

  it("refreshes the private cover URL and serves the image from a stable public URL", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array([1, 2, 3]), {
      headers: { "content-type": "image/png", "content-length": "3" }
    })));

    const response = await GET(new Request("https://sell.test/cover"), { params });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("image/png");
    expect(response.headers.get("cross-origin-resource-policy")).toBe("cross-origin");
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
    expect(fetch).toHaveBeenCalledWith(
      "https://storage.test/cover?X-Amz-Signature=fresh",
      { cache: "no-store", redirect: "error", signal: expect.any(AbortSignal) }
    );
  });

  it("does not expose a URL when the published product has no cover", async () => {
    readPublicStorefront.mockResolvedValue(storefront(false));
    const response = await GET(new Request("https://sell.test/cover"), { params });
    expect(response.status).toBe(404);
  });

  it("fails closed when storage returns a different media type", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not an image", {
      headers: { "content-type": "text/plain" }
    })));
    const response = await GET(new Request("https://sell.test/cover"), { params });
    expect(response.status).toBe(503);
  });
});
