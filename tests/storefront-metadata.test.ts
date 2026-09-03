import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { readPublicStorefront } = vi.hoisted(() => ({ readPublicStorefront: vi.fn() }));
vi.mock("@/lib/internal-api-client.server", async (original) => {
  const real = await original<typeof import("@/lib/internal-api-client.server")>();
  return { ...real, readPublicStorefront };
});

import { generateMetadata as storefrontMetadata } from "../app/s/[sellerSlug]/page";
import { generateMetadata as productMetadata } from "../app/s/[sellerSlug]/p/[productSlug]/page";

const banner = { url: "https://storage.test/b?X-Amz-Signature=curta", media_type: "image/jpeg", width: 1600, height: 500, expires_in_seconds: 60 };
const logo = { url: "https://storage.test/l?X-Amz-Signature=curta", media_type: "image/jpeg", width: 1081, height: 1081, expires_in_seconds: 60 };

function vitrine(seller: Record<string, unknown>, product: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    seller: { id: "11111111-1111-4111-8111-111111111111", slug: "hub2you", name: "Hub2you", ...seller },
    products: [{
      id: "22222222-2222-4222-8222-222222222222",
      slug: "gerador-de-cotacoes",
      title: "Gerador de cotações",
      description: "Compare seguros em minutos.",
      publication_id: "33333333-3333-4333-8333-333333333333",
      publication_version: 1,
      price: { unit_amount_minor: 4700, currency: "BRL", billing_period: "one_time" },
      ...product
    }]
  };
}

/** O tipo `twitter` do Next é uma união em que nem todo membro declara
 *  `card`; aqui só interessa o valor que a página escreveu. */
function twitterCard(meta: { twitter?: unknown }): string | undefined {
  const twitter = meta.twitter as { card?: string } | undefined;
  return twitter?.card;
}

describe("metadata das páginas públicas", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("a vitrine leva a marca da loja no card, e não o ícone da plataforma", async () => {
    readPublicStorefront.mockResolvedValue(vitrine({ logo, banner, tagline: "IA para corretores" }));
    const meta = await storefrontMetadata({ params: Promise.resolve({ sellerSlug: "hub2you" }) });

    expect(meta.openGraph?.images).toEqual([{
      url: "https://sell.autonomia.site/api/storefronts/hub2you/brand/banner",
      width: 1600,
      height: 500,
      alt: "Banner de Hub2you"
    }]);
    expect(twitterCard(meta)).toBe("summary_large_image");
  });

  it("loja sem marca nenhuma não declara imagem, em vez de declarar uma quebrada", async () => {
    readPublicStorefront.mockResolvedValue(vitrine({}));
    const meta = await storefrontMetadata({ params: Promise.resolve({ sellerSlug: "hub2you" }) });
    expect(meta.openGraph?.images).toBeUndefined();
    expect(twitterCard(meta)).toBe("summary");
  });

  it("o produto passa a ter título e descrição próprios, com a imagem dele", async () => {
    readPublicStorefront.mockResolvedValue(vitrine({ logo }, { banner, headline: "Cotação comparativa em 2 minutos" }));
    const meta = await productMetadata({
      params: Promise.resolve({ sellerSlug: "hub2you", productSlug: "gerador-de-cotacoes" })
    });

    expect(meta.title).toBe("Gerador de cotações, por Hub2you");
    expect(meta.description).toBe("Cotação comparativa em 2 minutos");
    expect(meta.alternates?.canonical).toBe("https://sell.autonomia.site/s/hub2you/p/gerador-de-cotacoes");
    expect(meta.openGraph?.images).toEqual([{
      url: "https://sell.autonomia.site/api/storefronts/hub2you/products/gerador-de-cotacoes/banner",
      width: 1600,
      height: 500,
      alt: "Banner de Gerador de cotações"
    }]);
  });

  it("produto sem promessa cai na descrição, e sem imagem própria cai na marca da loja", async () => {
    readPublicStorefront.mockResolvedValue(vitrine({ logo }));
    const meta = await productMetadata({
      params: Promise.resolve({ sellerSlug: "hub2you", productSlug: "gerador-de-cotacoes" })
    });

    expect(meta.description).toBe("Compare seguros em minutos.");
    expect(meta.openGraph?.images).toEqual([{
      url: "https://sell.autonomia.site/api/storefronts/hub2you/brand/logo",
      width: 1081,
      height: 1081,
      alt: "Logo de Hub2you"
    }]);
    expect(twitterCard(meta)).toBe("summary");
  });
});
