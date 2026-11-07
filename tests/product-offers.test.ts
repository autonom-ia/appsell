import { describe, expect, it } from "vitest";

import {
  canBecomeDefault,
  canCreateOffer,
  MAX_OFFERS_PER_PRODUCT,
  offerErrorMessage,
  offerFailureMessage,
  orderedOffers,
  readMultiOffer,
  type ProductOfferSummary
} from "../app/components/product-offers";

// Leitura do painel de ofertas (#553, fase 2b). A flag vem só da api; qualquer
// coisa fora do formato desliga o painel, e o editor fica como era.

const PADRAO = {
  id: "o1",
  title: "Plano único",
  summary: "",
  status: "active",
  version: 4,
  is_default: true,
  unit_amount_minor: 9700,
  currency: "BRL",
  billing_period: "one_time"
};
const EXTRA = { ...PADRAO, id: "o2", title: "Plano anual", status: "draft", version: 1, is_default: false };

function detalhe(product: Record<string, unknown>) {
  return { product: { id: "p1", ...product } };
}

describe("readMultiOffer", () => {
  it("desliga quando a api não manda a flag, como na Hub2You hoje", () => {
    expect(readMultiOffer(detalhe({})).enabled).toBe(false);
    expect(readMultiOffer(detalhe({ multi_offer_enabled: false, offers: [PADRAO] })).enabled).toBe(false);
    expect(readMultiOffer(null).enabled).toBe(false);
  });

  it("só o true literal liga; texto ou número não", () => {
    for (const valor of ["true", 1, "1"]) {
      expect(readMultiOffer(detalhe({ multi_offer_enabled: valor, offers: [PADRAO] })).enabled).toBe(false);
    }
  });

  it("oferta malformada desliga tudo em vez de mostrar lista pela metade", () => {
    const quebrada = { ...EXTRA, version: "1" };
    const estado = readMultiOffer(detalhe({ multi_offer_enabled: true, offers: [PADRAO, quebrada] }));
    expect(estado).toEqual({ enabled: false, offers: [] });
    const statusDesconhecido = { ...EXTRA, status: "paused" };
    expect(readMultiOffer(detalhe({ multi_offer_enabled: true, offers: [statusDesconhecido] })).enabled)
      .toBe(false);
  });

  it("lê as ofertas quando a api liga a loja", () => {
    const estado = readMultiOffer(detalhe({ multi_offer_enabled: true, offers: [EXTRA, PADRAO] }));
    expect(estado.enabled).toBe(true);
    expect(orderedOffers(estado.offers).map((offer) => offer.id)).toEqual(["o1", "o2"]);
    expect(Object.isFrozen(estado.offers)).toBe(true);
  });
});

describe("regras de botão, espelho da api", () => {
  const estado = readMultiOffer(detalhe({ multi_offer_enabled: true, offers: [PADRAO, EXTRA] }));
  const [padrao, extra] = orderedOffers(estado.offers) as [ProductOfferSummary, ProductOfferSummary];

  it("rascunho não vira padrão de produto à venda", () => {
    expect(canBecomeDefault(extra, estado.offers)).toBe(false);
    const aVenda = { ...extra, status: "active" as const };
    expect(canBecomeDefault(aVenda, [padrao, aVenda])).toBe(true);
  });

  it("a padrão e a encerrada não ganham o botão", () => {
    expect(canBecomeDefault(padrao, estado.offers)).toBe(false);
    expect(canBecomeDefault({ ...extra, status: "archived" }, estado.offers)).toBe(false);
  });

  it("produto sem venda aceita rascunho como padrão", () => {
    const padraoRascunho = { ...padrao, status: "draft" as const };
    expect(canBecomeDefault(extra, [padraoRascunho, extra])).toBe(true);
  });

  it("o formulário some no limite da api", () => {
    const cheias = Array.from({ length: MAX_OFFERS_PER_PRODUCT }, (_, i) => ({ ...extra, id: `x${i}` }));
    expect(canCreateOffer(cheias)).toBe(false);
    expect(canCreateOffer(cheias.slice(1))).toBe(true);
  });
});

describe("offerErrorMessage", () => {
  it("cada recusa da api tem a sua mensagem", () => {
    const codigos = ["multi_offer_disabled", "multi_offer_misconfigured", "offer_limit_reached", "default_offer_locked"];
    const mensagens = codigos.map((codigo) => offerErrorMessage(409, codigo));
    expect(new Set(mensagens).size).toBe(codigos.length);
    expect(offerErrorMessage(403, "multi_offer_disabled")).toContain("ainda não pode");
  });

  it("código desconhecido cai na mensagem pelo status", () => {
    expect(offerErrorMessage(409, "outro")).toContain("Recarregue");
    expect(offerErrorMessage(500, undefined)).toContain("Tente de novo");
  });
});

describe("offerFailureMessage (#546)", () => {
  it("o 422 de preço mínimo diz o menor preço, não o genérico", () => {
    // Pega: o produtor vendo "Confira os campos do preço" sem saber qual preço serve.
    const body = { error: "invalid_product_input", field: "unit_amount_minor", reason: "below_minimum", minimum_minor: 52 };
    const message = offerFailureMessage(422, body).replaceAll("\u00a0", " ");
    expect(message).toContain("O menor preço possível é R$ 0,52");
  });

  it("as demais recusas seguem as mensagens do painel", () => {
    expect(offerFailureMessage(409, { error: "offer_limit_reached" })).toBe(offerErrorMessage(409, "offer_limit_reached"));
    expect(offerFailureMessage(422, { error: "invalid_product_input" })).toBe(offerErrorMessage(422, "invalid_product_input"));
    expect(offerFailureMessage(503, null)).toBe(offerErrorMessage(503, undefined));
  });
});
