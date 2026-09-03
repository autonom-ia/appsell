import { describe, expect, it } from "vitest";

import type { PublicStorefrontProduct, PublicStorefrontSeller } from "@/lib/public-storefront";
import { productCardImage, storefrontCardImage, twitterCardKind } from "@/lib/social-card";

function imagem(width: number, height: number) {
  return { url: "https://storage.test/x?X-Amz-Signature=curta", media_type: "image/jpeg", width, height, expires_in_seconds: 60 };
}

const loja: PublicStorefrontSeller = {
  id: "11111111-1111-4111-8111-111111111111",
  slug: "hub2you",
  name: "Hub2you"
};

const produto: PublicStorefrontProduct = {
  id: "22222222-2222-4222-8222-222222222222",
  slug: "gerador-de-cotacoes",
  title: "Gerador de cotações",
  description: "Curso",
  publication_id: "33333333-3333-4333-8333-333333333333",
  publication_version: 1,
  price: null
};

describe("imagem do card social", () => {
  it("nunca devolve a URL assinada do armazenamento, que expira em 60 segundos", () => {
    const card = storefrontCardImage({ ...loja, logo: imagem(1081, 1081) });
    expect(card?.url).toBe("https://sell.autonomia.site/api/storefronts/hub2you/brand/logo");
    expect(card?.url).not.toContain("X-Amz-Signature");
  });

  it("prefere o banner da loja ao logo", () => {
    const card = storefrontCardImage({ ...loja, logo: imagem(1081, 1081), banner: imagem(1600, 500) });
    expect(card?.url).toContain("/brand/banner");
    expect(card).toMatchObject({ width: 1600, height: 500 });
  });

  it("não inventa imagem para loja sem marca", () => {
    expect(storefrontCardImage(loja)).toBeUndefined();
  });

  it("no produto, banner vem antes da capa", () => {
    const card = productCardImage(loja, { ...produto, cover: imagem(1672, 941), banner: imagem(1600, 500) });
    expect(card?.url).toBe("https://sell.autonomia.site/api/storefronts/hub2you/products/gerador-de-cotacoes/banner");
  });

  it("produto sem banner cai na capa", () => {
    const card = productCardImage(loja, { ...produto, cover: imagem(1672, 941) });
    expect(card?.url).toContain("/products/gerador-de-cotacoes/cover");
  });

  it("produto sem imagem nenhuma usa a marca da loja, para não cair no ícone da plataforma", () => {
    const card = productCardImage({ ...loja, logo: imagem(1081, 1081) }, produto);
    expect(card?.url).toContain("/brand/logo");
  });

  it("escapa o endereço da loja e do produto", () => {
    const card = productCardImage({ ...loja, slug: "a/b" }, { ...produto, slug: "c d", cover: imagem(16, 9) });
    expect(card?.url).toBe("https://sell.autonomia.site/api/storefronts/a%2Fb/products/c%20d/cover");
  });

  it("card largo só para imagem larga: logo quadrado fica na miniatura", () => {
    expect(twitterCardKind({ url: "u", width: 1600, height: 500, alt: "" })).toBe("summary_large_image");
    expect(twitterCardKind({ url: "u", width: 1672, height: 941, alt: "" })).toBe("summary_large_image");
    expect(twitterCardKind({ url: "u", width: 1081, height: 1081, alt: "" })).toBe("summary");
    expect(twitterCardKind(undefined)).toBe("summary");
    expect(twitterCardKind({ url: "u", width: 1081, height: 0, alt: "" })).toBe("summary");
  });
});
