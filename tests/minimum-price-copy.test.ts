import { describe, expect, it } from "vitest";

import { minimumFromResponse, minimumPriceMessage } from "../lib/minimum-price-copy";

describe("texto do preço mínimo (#546)", () => {
  it("diz o que fazer, com o menor preço em reais", () => {
    const message = minimumPriceMessage(52);
    expect(message).toContain("a loja não recebe nada");
    expect(message).toContain("0,52");
    // Pega: jargão interno vazando para o produtor.
    for (const jargon of ["application_fee", "minor", "net", "below_minimum", "Stripe"]) {
      expect(message).not.toContain(jargon);
    }
  });

  it("lê o mínimo só da resposta de preço abaixo do mínimo", () => {
    expect(minimumFromResponse({ field: "offer_unit_amount_minor", reason: "below_minimum", minimum_minor: 52 })).toBe(52);
    expect(minimumFromResponse({ field: "offer_unit_amount_minor" })).toBeNull();
    expect(minimumFromResponse({ reason: "below_minimum", minimum_minor: "52" })).toBeNull();
    expect(minimumFromResponse({ reason: "below_minimum", minimum_minor: 0 })).toBeNull();
    expect(minimumFromResponse(null)).toBeNull();
  });
});
