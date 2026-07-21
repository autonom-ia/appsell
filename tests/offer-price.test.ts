import { describe, expect, it } from "vitest";

import {
  billingPeriodLabel,
  formatOfferPrice,
  formatOfferPriceBRL,
  isOfferBillingPeriod,
  MAX_UNIT_AMOUNT_MINOR,
  parsePriceInputToMinor,
  priceInputFromMinor
} from "../lib/offer-price";

describe("parsePriceInputToMinor", () => {
  it("treats an empty input as an unpriced draft offer (0 centavos)", () => {
    expect(parsePriceInputToMinor("")).toBe(0);
    expect(parsePriceInputToMinor("   ")).toBe(0);
  });

  it("parses integer reais into centavos", () => {
    expect(parsePriceInputToMinor("1")).toBe(100);
    expect(parsePriceInputToMinor("49")).toBe(4_900);
  });

  it("parses comma and dot decimals with one or two digits", () => {
    expect(parsePriceInputToMinor("49,90")).toBe(4_990);
    expect(parsePriceInputToMinor("49.90")).toBe(4_990);
    expect(parsePriceInputToMinor("49,9")).toBe(4_990);
    expect(parsePriceInputToMinor("0,01")).toBe(1);
  });

  it("rejects malformed input", () => {
    expect(parsePriceInputToMinor("R$ 10")).toBeNull();
    expect(parsePriceInputToMinor("1 2,3 4")).toBeNull();
    expect(parsePriceInputToMinor("12\u00a030")).toBeNull();
    expect(parsePriceInputToMinor("12\t30")).toBeNull();
    expect(parsePriceInputToMinor("10,999")).toBeNull();
    expect(parsePriceInputToMinor("-5")).toBeNull();
    expect(parsePriceInputToMinor("1.234,56")).toBeNull();
    expect(parsePriceInputToMinor("abc")).toBeNull();
  });

  it("caps at the offer column ceiling", () => {
    expect(parsePriceInputToMinor("999999999,99")).toBe(MAX_UNIT_AMOUNT_MINOR);
    expect(parsePriceInputToMinor("1000000000")).toBeNull();
  });
});

describe("priceInputFromMinor", () => {
  it("round-trips values produced by the parser", () => {
    for (const input of ["1", "49", "49,90", "0,01", "999999999,99"]) {
      const minor = parsePriceInputToMinor(input);
      expect(minor).not.toBeNull();
      expect(parsePriceInputToMinor(priceInputFromMinor(minor))).toBe(minor);
    }
  });

  it("maps unpriced and invalid values to an empty input", () => {
    expect(priceInputFromMinor(0)).toBe("");
    expect(priceInputFromMinor(null)).toBe("");
    expect(priceInputFromMinor(undefined)).toBe("");
    expect(priceInputFromMinor(-100)).toBe("");
    expect(priceInputFromMinor(10.5)).toBe("");
  });
});

describe("formatOfferPriceBRL", () => {
  it("formats centavos as pt-BR currency", () => {
    expect(formatOfferPriceBRL(100).replace(/ /g, " ")).toBe("R$ 1,00");
    expect(formatOfferPriceBRL(4_990).replace(/ /g, " ")).toBe("R$ 49,90");
  });
});

describe("formatOfferPrice (currency-aware)", () => {
  it("uses the given currency symbol", () => {
    expect(formatOfferPrice(4_990, "USD").replace(/\u00a0/g, " ")).toMatch(/US\$|\$/);
    expect(formatOfferPrice(4_990, "BRL").replace(/\u00a0/g, " ")).toBe("R$ 49,90");
  });

  it("falls back to BRL for an invalid currency instead of a wrong symbol", () => {
    expect(formatOfferPrice(100, "xxx").replace(/\u00a0/g, " ")).toBe("R$ 1,00");
    expect(formatOfferPrice(100, null).replace(/\u00a0/g, " ")).toBe("R$ 1,00");
  });
});

describe("billing period helpers", () => {
  it("labels known periods and falls back to one_time", () => {
    expect(billingPeriodLabel("one_time")).toBe("Pagamento único");
    expect(billingPeriodLabel("monthly")).toBe("Assinatura mensal");
    expect(billingPeriodLabel("yearly")).toBe("Assinatura anual");
    expect(billingPeriodLabel("weekly")).toBe("Pagamento único");
  });

  it("narrows valid billing periods", () => {
    expect(isOfferBillingPeriod("monthly")).toBe(true);
    expect(isOfferBillingPeriod("weekly")).toBe(false);
    expect(isOfferBillingPeriod(null)).toBe(false);
  });
});
