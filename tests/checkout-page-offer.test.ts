import { beforeEach, describe, expect, it, vi } from "vitest";

// #154, achado 3: a página de checkout separa "a loja não respondeu" de "este
// produto não está à venda". Antes, qualquer falha virava a mesma mensagem.

vi.mock("server-only", () => ({}));

const { readPublicStorefront, isProductCheckoutAvailable, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    readPublicStorefront: vi.fn<(slug: string) => Promise<unknown>>(),
    isProductCheckoutAvailable: vi.fn<(seller: string, product: string) => boolean>(() => true),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({ readPublicStorefront, DomainApiError }));
vi.mock("@/lib/product-checkout-availability.server", () => ({ isProductCheckoutAvailable }));

import { readCheckoutPageOffer } from "../lib/checkout-page-offer.server";

const SELLER = "loja";
const PRODUCT = "curso";

function storefront(price: unknown) {
  return {
    schema_version: 1,
    seller: { id: "11111111-1111-4111-8111-111111111111", slug: SELLER, name: "Loja Teste" },
    products: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        slug: PRODUCT,
        title: "Curso Teste",
        description: "Curso",
        publication_id: "33333333-3333-4333-8333-333333333333",
        publication_version: 1,
        price
      }
    ]
  };
}

const ONE_TIME = { unit_amount_minor: 4_700, currency: "BRL", billing_period: "one_time" };

describe("readCheckoutPageOffer", () => {
  beforeEach(() => {
    readPublicStorefront.mockReset();
    isProductCheckoutAvailable.mockReset();
    isProductCheckoutAvailable.mockReturnValue(true);
  });

  it("produto à venda devolve loja, título e preço formatado", async () => {
    readPublicStorefront.mockResolvedValue(storefront(ONE_TIME));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({
      kind: "ready",
      sellerName: "Loja Teste",
      productTitle: "Curso Teste",
      priceLabel: "R$ 47,00"
    });
  });

  it("loja fora do ar ou resposta fora do formato é indisponível, não sem preço", async () => {
    readPublicStorefront.mockRejectedValueOnce(new DomainApiError(503, "unavailable"));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "unavailable" });
    readPublicStorefront.mockRejectedValueOnce(new TypeError("fetch failed"));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "unavailable" });
    readPublicStorefront.mockResolvedValueOnce({ schema_version: 2 });
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "unavailable" });
  });

  it("loja inexistente, produto ausente ou sem preço cobrável é fora de venda", async () => {
    readPublicStorefront.mockRejectedValueOnce(new DomainApiError(404, "not_found"));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "not_for_sale" });
    readPublicStorefront.mockResolvedValueOnce(storefront(ONE_TIME));
    expect(await readCheckoutPageOffer(SELLER, "outro")).toEqual({ kind: "not_for_sale" });
    readPublicStorefront.mockResolvedValueOnce(storefront(null));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "not_for_sale" });
    readPublicStorefront.mockResolvedValueOnce(storefront({ ...ONE_TIME, billing_period: "monthly" }));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "not_for_sale" });
  });

  it("moeda estrangeira e preço zero são fora de venda: o portão é o mesmo do create-session", async () => {
    readPublicStorefront.mockResolvedValueOnce(storefront({ ...ONE_TIME, currency: "USD" }));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "not_for_sale" });
    readPublicStorefront.mockResolvedValueOnce(storefront({ ...ONE_TIME, unit_amount_minor: 0 }));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "not_for_sale" });
  });

  it("preço negativo ou fracionado nem passa da leitura da vitrine: é resposta fora do formato", async () => {
    // parsePublicStorefront recusa a vitrine inteira (inteiro, >= 0). A página
    // não mostra preço nenhum e trata como resposta quebrada da api.
    readPublicStorefront.mockResolvedValueOnce(storefront({ ...ONE_TIME, unit_amount_minor: -100 }));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "unavailable" });
    readPublicStorefront.mockResolvedValueOnce(storefront({ ...ONE_TIME, unit_amount_minor: 47.5 }));
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "unavailable" });
  });

  it("produto sem checkout configurado é fora de venda, sem ler a loja", async () => {
    isProductCheckoutAvailable.mockReturnValue(false);
    expect(await readCheckoutPageOffer(SELLER, PRODUCT)).toEqual({ kind: "not_for_sale" });
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });
});
