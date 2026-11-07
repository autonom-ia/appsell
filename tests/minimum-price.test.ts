import { describe, expect, it } from "vitest";

import { directChargeApplicationFeeCents } from "../lib/checkout-fee";
import {
  STRIPE_MINIMUM_CHARGE_BRL_MINOR,
  checkSellerNet,
  minimumChargeMinor
} from "../lib/minimum-price";

// Preço mínimo (#546): a loja tem de receber algo. São duas condições: o valor
// cobrado (preço menos desconto) passa do mínimo de cobrança da Stripe em BRL,
// e o líquido (cobrado menos comissão menos custo da loja) é positivo.

describe("checkSellerNet", () => {
  it("aceita preço comum e devolve líquido e comissão", () => {
    // R$47,00 no launch: floor(4700*550/10000)=258 + 49 = 307.
    expect(checkSellerNet({ amountMinor: 4_700, currency: "BRL" })).toEqual({
      ok: true,
      feeMinor: 307,
      netMinor: 4_393
    });
  });

  it("recusa preço igual à comissão: a loja receberia zero", () => {
    // R$0,51 no launch: comissão floor(2,805)=2 + 49 = 51, líquido 0.
    expect(directChargeApplicationFeeCents(51)).toBe(51);
    expect(checkSellerNet({ amountMinor: 51, currency: "BRL" })).toEqual({
      ok: false,
      reason: "below_minimum",
      minimumMinor: 52,
      netMinor: 0
    });
  });

  it("aceita o primeiro centavo em que a loja recebe algo", () => {
    expect(checkSellerNet({ amountMinor: 52, currency: "BRL" })).toEqual({
      ok: true,
      feeMinor: 51,
      netMinor: 1
    });
  });

  it("recusa abaixo do mínimo de cobrança da Stripe mesmo com comissão menor", () => {
    // No scale, R$0,49 daria líquido positivo, mas a Stripe não cobra menos de R$0,50.
    expect(STRIPE_MINIMUM_CHARGE_BRL_MINOR).toBe(50);
    const result = checkSellerNet({ amountMinor: 49, currency: "BRL", planKey: "scale" });
    expect(result).toMatchObject({ ok: false, reason: "below_minimum", minimumMinor: 50 });
  });

  it("recusa desconto de 100%: não sobra nada para cobrar", () => {
    const result = checkSellerNet({ amountMinor: 4_700, discountMinor: 4_700, currency: "BRL" });
    expect(result).toEqual({
      ok: false,
      reason: "below_minimum",
      minimumMinor: 4_752,
      netMinor: 0
    });
  });

  it("usa o mesmo desconto e o mesmo custo no mínimo que sugere", () => {
    // Desconto de R$10 e custo da loja de R$1: o mínimo sugerido deixa líquido 1.
    const result = checkSellerNet({
      amountMinor: 1_000,
      discountMinor: 1_000,
      sellerBorneCostMinor: 100,
      currency: "BRL"
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    const charged = result.minimumMinor - 1_000;
    expect(charged - directChargeApplicationFeeCents(charged) - 100).toBe(1);
    expect(
      checkSellerNet({
        amountMinor: result.minimumMinor - 1,
        discountMinor: 1_000,
        sellerBorneCostMinor: 100,
        currency: "BRL"
      }).ok
    ).toBe(false);
    expect(
      checkSellerNet({
        amountMinor: result.minimumMinor,
        discountMinor: 1_000,
        sellerBorneCostMinor: 100,
        currency: "BRL"
      }).ok
    ).toBe(true);
  });

  it("acha o mínimo mesmo com custo grande, sem varrer centavo por centavo", () => {
    const result = checkSellerNet({
      amountMinor: 100,
      sellerBorneCostMinor: 50_000_000,
      currency: "BRL"
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    const charged = result.minimumMinor;
    expect(charged - directChargeApplicationFeeCents(charged) - 50_000_000).toBe(1);
  });

  it("segue o plano escolhido", () => {
    expect(checkSellerNet({ amountMinor: 50, currency: "BRL", planKey: "growth" })).toMatchObject({
      ok: true
    });
    expect(checkSellerNet({ amountMinor: 50, currency: "BRL" })).toMatchObject({ ok: false });
  });

  it.each([
    { amountMinor: 0 },
    { amountMinor: -1 },
    { amountMinor: 10.5 },
    { amountMinor: Number.NaN },
    { amountMinor: 100, discountMinor: -1 },
    { amountMinor: 100, discountMinor: 0.5 },
    { amountMinor: 100, sellerBorneCostMinor: -1 },
    { amountMinor: 100_000_000_000 }
  ])("lança erro em entrada inválida: %o", (input) => {
    expect(() => checkSellerNet({ ...input, currency: "BRL" })).toThrow(RangeError);
  });

  it("lança erro em moeda que não é BRL", () => {
    expect(() =>
      checkSellerNet({ amountMinor: 100, currency: "USD" as unknown as "BRL" })
    ).toThrow(RangeError);
  });
});

describe("minimumChargeMinor", () => {
  it("é o menor preço sem desconto nem custo em que a loja recebe algo", () => {
    expect(minimumChargeMinor("BRL")).toBe(52);
    expect(minimumChargeMinor("BRL", "growth")).toBe(50);
    expect(minimumChargeMinor("BRL", "scale")).toBe(50);
  });

  it("lança erro em moeda que não é BRL", () => {
    expect(() => minimumChargeMinor("EUR" as unknown as "BRL")).toThrow(RangeError);
  });
});
