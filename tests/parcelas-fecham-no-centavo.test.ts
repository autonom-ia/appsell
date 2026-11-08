import { describe, expect, it } from "vitest";

import { MAX_UNIT_AMOUNT_MINOR } from "../lib/offer-price";
import {
  InstallmentError,
  installmentOptions,
  installmentPlan,
  installmentSummary
} from "../lib/pricing/installments";

const sum = (values: readonly number[]) => values.reduce((total, value) => total + value, 0);

describe("sem juros: a soma das parcelas é o preço, e o resto vai na primeira", () => {
  it("R$ 100,00 em 3x vira 33,34 + 33,33 + 33,33", () => {
    const plan = installmentPlan({ principalMinor: 10_000, count: 3, monthlyRateBasisPoints: 0, interestPayer: "seller" });
    expect(plan.installmentsMinor).toEqual([3_334, 3_333, 3_333]);
    expect(plan.totalMinor).toBe(10_000);
    expect(plan.interestMinor).toBe(0);
  });

  it("divisão exata não gera resto", () => {
    const plan = installmentPlan({ principalMinor: 9_000, count: 3, monthlyRateBasisPoints: 0, interestPayer: "buyer" });
    expect(plan.installmentsMinor).toEqual([3_000, 3_000, 3_000]);
  });

  it("o resto nunca passa de count - 1 centavos e só a primeira parcela o recebe", () => {
    for (const principalMinor of [1_201, 4_700, 8_700, 99_999, 123_457]) {
      for (let count = 1; count <= 12; count += 1) {
        if (principalMinor < count) continue;
        const plan = installmentPlan({ principalMinor, count, monthlyRateBasisPoints: 0, interestPayer: "seller" });
        const [first, ...rest] = plan.installmentsMinor;
        expect(sum(plan.installmentsMinor)).toBe(principalMinor);
        expect(new Set(rest).size).toBeLessThanOrEqual(1);
        expect(first! - (rest[0] ?? first!)).toBeGreaterThanOrEqual(0);
        expect(first! - (rest[0] ?? first!)).toBeLessThan(count);
      }
    }
  });

  it("à vista é uma parcela só, do preço inteiro", () => {
    const plan = installmentPlan({ principalMinor: 4_700, count: 1, monthlyRateBasisPoints: 299, interestPayer: "buyer" });
    expect(plan.installmentsMinor).toEqual([4_700]);
    expect(plan.interestMinor).toBe(0);
  });
});

describe("juros do comprador (Tabela Price): o total é arredondado antes de dividir", () => {
  it("R$ 1.000,00 em 12x a 1,99% a.m. fecha no centavo", () => {
    // Conta exata (frações): total = 12 · 1000 · 0,0199 · 1,0199^12 / (1,0199^12 − 1) = 1134,0183... → 1134,02
    const plan = installmentPlan({ principalMinor: 100_000, count: 12, monthlyRateBasisPoints: 199, interestPayer: "buyer" });
    expect(plan.totalMinor).toBe(113_402);
    expect(plan.interestMinor).toBe(13_402);
    expect(sum(plan.installmentsMinor)).toBe(plan.totalMinor);
    expect(plan.installmentsMinor).toEqual([9_452, ...Array(11).fill(9_450)]);
  });

  it("R$ 87,00 em 2x a 2,99% a.m.", () => {
    // total = 2 · 87 · 0,0299 · 1,0299^2 / (1,0299^2 − 1) = 90,9211... → 90,92
    const plan = installmentPlan({ principalMinor: 8_700, count: 2, monthlyRateBasisPoints: 299, interestPayer: "buyer" });
    expect(plan.totalMinor).toBe(9_092);
    expect(plan.installmentsMinor).toEqual([4_546, 4_546]);
  });

  it("arredonda para o centavo mais próximo, com conta exata e sem ponto flutuante", () => {
    // 3 centavos em 3x a 50% a.m.: total = 243/38 = 6,39... → 6
    expect(installmentPlan({ principalMinor: 3, count: 3, monthlyRateBasisPoints: 5_000, interestPayer: "buyer" }).totalMinor).toBe(6);
    // 7 centavos em 2x a 100% a.m.: total = 56/3 = 18,66... → 19
    expect(installmentPlan({ principalMinor: 7, count: 2, monthlyRateBasisPoints: 10_000, interestPayer: "buyer" }).totalMinor).toBe(19);
    // 10 centavos em 2x a 50% a.m.: total = 18 exatos
    expect(installmentPlan({ principalMinor: 10, count: 2, monthlyRateBasisPoints: 5_000, interestPayer: "buyer" }).totalMinor).toBe(18);
  });

  it("meio centavo exato sobe", () => {
    // 19 centavos em 3x a 50% a.m.: total = 81/2 = 40,5 → 41
    expect(installmentPlan({ principalMinor: 19, count: 3, monthlyRateBasisPoints: 5_000, interestPayer: "buyer" }).totalMinor).toBe(41);
    // 9 centavos em 2x a 25% a.m.: total = 25/2 = 12,5 → 13
    expect(installmentPlan({ principalMinor: 9, count: 2, monthlyRateBasisPoints: 2_500, interestPayer: "buyer" }).totalMinor).toBe(13);
  });

  it("R$ 47,00 a 1,99% a.m., de 1x a 12x, com o total exato de cada prazo", () => {
    // Conferido em fractions.Fraction, arredondando meio centavo para cima.
    const esperados = [4_700, 4_841, 4_888, 4_936, 4_984, 5_033, 5_081, 5_131, 5_180, 5_230, 5_280, 5_330];
    const totais = esperados.map((_, index) =>
      installmentPlan({ principalMinor: 4_700, count: index + 1, monthlyRateBasisPoints: 199, interestPayer: "buyer" }).totalMinor
    );
    expect(totais).toEqual(esperados);
  });

  it("o total com juros nunca é menor que o preço", () => {
    for (const rate of [1, 99, 199, 349, 999]) {
      for (let count = 2; count <= 12; count += 1) {
        const plan = installmentPlan({ principalMinor: 4_700, count, monthlyRateBasisPoints: rate, interestPayer: "buyer" });
        expect(plan.totalMinor).toBeGreaterThanOrEqual(4_700);
        expect(sum(plan.installmentsMinor)).toBe(plan.totalMinor);
      }
    }
  });
});

describe("juros da loja: o comprador paga o preço, dividido sem acréscimo", () => {
  it("a taxa não muda o que o comprador vê", () => {
    const plan = installmentPlan({ principalMinor: 10_000, count: 3, monthlyRateBasisPoints: 299, interestPayer: "seller" });
    expect(plan.totalMinor).toBe(10_000);
    expect(plan.interestMinor).toBe(0);
    expect(plan.interestPayer).toBe("seller");
  });
});

describe("entradas inválidas falham alto, nunca viram outro valor", () => {
  const base = { principalMinor: 10_000, count: 3, monthlyRateBasisPoints: 0, interestPayer: "buyer" as const };

  it.each([
    ["preço zero", { principalMinor: 0 }],
    ["preço negativo", { principalMinor: -1 }],
    ["preço fracionado", { principalMinor: 10.5 }],
    ["preço acima do teto da oferta", { principalMinor: 100_000_000_000 }],
    ["zero parcelas", { count: 0 }],
    ["parcela fracionada", { count: 2.5 }],
    ["mais de 12 parcelas", { count: 13 }],
    ["juros negativos", { monthlyRateBasisPoints: -1 }],
    ["juros fracionados", { monthlyRateBasisPoints: 1.5 }],
    ["juros acima de 100% a.m.", { monthlyRateBasisPoints: 10_001 }],
    ["mais parcelas que centavos", { principalMinor: 2, count: 3 }]
  ])("%s", (_name, override) => {
    expect(() => installmentPlan({ ...base, ...override })).toThrow(InstallmentError);
  });

  it.each([
    ["NaN", { principalMinor: Number.NaN }],
    ["Infinity", { principalMinor: Number.POSITIVE_INFINITY }],
    ["-Infinity", { count: Number.NEGATIVE_INFINITY }],
    ["-0", { principalMinor: -0 }],
    ["texto", { principalMinor: "10000" as never }],
    ["bigint", { count: 3n as never }],
    ["juros NaN", { monthlyRateBasisPoints: Number.NaN }]
  ])("valor que não é inteiro seguro: %s", (_name, override) => {
    expect(() => installmentPlan({ ...base, ...override })).toThrow(InstallmentError);
  });

  it("aceita os limites exatos", () => {
    expect(installmentPlan({ ...base, principalMinor: MAX_UNIT_AMOUNT_MINOR, count: 12, interestPayer: "seller" }).totalMinor)
      .toBe(MAX_UNIT_AMOUNT_MINOR);
    expect(installmentPlan({ ...base, principalMinor: 12, count: 12 }).installmentsMinor).toEqual(Array(12).fill(1));
    expect(installmentPlan({ ...base, count: 2, monthlyRateBasisPoints: 10_000 }).totalMinor).toBe(26_667);
  });

  it("recusa o total com juros acima do teto da oferta", () => {
    expect(() => installmentPlan({ principalMinor: MAX_UNIT_AMOUNT_MINOR, count: 12, monthlyRateBasisPoints: 199, interestPayer: "buyer" }))
      .toThrow(InstallmentError);
  });

  it("o plano não pode ser alterado depois de calculado", () => {
    const plan = installmentPlan(base);
    expect(Object.isFrozen(plan)).toBe(true);
    expect(Object.isFrozen(plan.installmentsMinor)).toBe(true);
  });

  it("quem paga os juros precisa ser comprador ou loja", () => {
    expect(() => installmentPlan({ ...base, interestPayer: "ninguem" as never })).toThrow(InstallmentError);
  });
});

describe("opções do checkout", () => {
  it("lista de 1x até o máximo, parando antes da parcela mínima", () => {
    const options = installmentOptions({ principalMinor: 4_700, maxCount: 12, monthlyRateBasisPoints: 0, interestPayer: "seller", minInstallmentMinor: 500 });
    expect(options.map((plan) => plan.count)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    for (const plan of options) expect(Math.min(...plan.installmentsMinor)).toBeGreaterThanOrEqual(500);
  });

  it("com juros do comprador, o corte usa a parcela já com juros", () => {
    const options = installmentOptions({ principalMinor: 4_700, maxCount: 12, monthlyRateBasisPoints: 299, interestPayer: "buyer", minInstallmentMinor: 500 });
    expect(options.map((plan) => plan.count)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
  });

  it.each([
    ["máximo zero", { maxCount: 0 }],
    ["máximo acima de 12", { maxCount: 13 }],
    ["máximo fracionado", { maxCount: 2.5 }],
    ["parcela mínima negativa", { minInstallmentMinor: -1 }],
    ["parcela mínima NaN", { minInstallmentMinor: Number.NaN }]
  ])("recusa opção inválida: %s", (_name, override) => {
    const options = { principalMinor: 4_700, maxCount: 12, monthlyRateBasisPoints: 0, interestPayer: "seller" as const, minInstallmentMinor: 500 };
    expect(() => installmentOptions({ ...options, ...override })).toThrow(InstallmentError);
  });

  it("à vista sempre existe, mesmo abaixo da parcela mínima", () => {
    const options = installmentOptions({ principalMinor: 300, maxCount: 12, monthlyRateBasisPoints: 0, interestPayer: "seller", minInstallmentMinor: 500 });
    expect(options.map((plan) => plan.count)).toEqual([1]);
  });
});

describe("texto do checkout: cada parcela, o total e quem paga os juros", () => {
  it("parcelas iguais", () => {
    const plan = installmentPlan({ principalMinor: 9_000, count: 3, monthlyRateBasisPoints: 0, interestPayer: "seller" });
    expect(installmentSummary(plan, "BRL")).toBe("3x de R$ 30,00 sem juros · total R$ 90,00");
  });

  it("primeira parcela maior aparece separada", () => {
    const plan = installmentPlan({ principalMinor: 10_000, count: 3, monthlyRateBasisPoints: 0, interestPayer: "seller" });
    expect(installmentSummary(plan, "BRL")).toBe("1ª de R$ 33,34 + 2x de R$ 33,33 sem juros · total R$ 100,00");
  });

  it("juros do comprador mostram o acréscimo", () => {
    const plan = installmentPlan({ principalMinor: 8_700, count: 2, monthlyRateBasisPoints: 299, interestPayer: "buyer" });
    expect(installmentSummary(plan, "BRL")).toBe("2x de R$ 45,46 com juros de 2,99% a.m. · total R$ 90,92 (R$ 3,92 de juros)");
  });

  it.each([["minúscula", "usd"], ["vazia", ""], ["curta", "BR"], ["indefinida", undefined as never], ["com acento", "RÉA"]])(
    "moeda inválida é erro, nunca vira BRL: %s",
    (_name, currency) => {
      const plan = installmentPlan({ principalMinor: 9_000, count: 3, monthlyRateBasisPoints: 0, interestPayer: "seller" });
      expect(() => installmentSummary(plan, currency)).toThrow(InstallmentError);
    }
  );

  it("à vista", () => {
    const plan = installmentPlan({ principalMinor: 4_700, count: 1, monthlyRateBasisPoints: 0, interestPayer: "seller" });
    expect(installmentSummary(plan, "BRL")).toBe("à vista R$ 47,00");
  });
});
