import { describe, expect, it } from "vitest";

import { publishedOnLabel, stepSummaries, type StepFacts } from "../lib/editor-steps";

function facts(overrides: Partial<StepFacts> = {}): StepFacts {
  return {
    title: "IA na prática",
    slug: "ia-na-pratica",
    modules: 2,
    lessons: 5,
    offerStatus: "draft",
    unitAmountMinor: 0,
    currency: "BRL",
    published: false,
    publishedAt: null,
    hasUnpublishedChanges: false,
    ...overrides
  };
}

describe("stepSummaries", () => {
  it("conta módulos e aulas em vez de repetir a dica fixa", () => {
    expect(stepSummaries(facts()).curriculo).toBe("2 módulos, 5 aulas");
  });

  it("concorda no singular", () => {
    expect(stepSummaries(facts({ modules: 1, lessons: 1 })).curriculo).toBe("1 módulo, 1 aula");
  });

  it("diz o que falta quando a etapa está vazia", () => {
    // "Pendente" era o rótulo que não significava nada. A frase nomeia a falta.
    expect(stepSummaries(facts({ modules: 0, lessons: 0 })).curriculo).toBe("Nenhum módulo ainda");
    expect(stepSummaries(facts({ title: "  " })).produto).toBe("Falta o nome ou o endereço");
  });
});

describe("resumo do preço", () => {
  it("sem preço é gratuito, e diz isso", () => {
    expect(stepSummaries(facts()).oferta).toBe("Gratuito");
  });

  it("preço salvo em rascunho não finge que já está vendendo", () => {
    // Dizer só "R$ 49,90" faria o vendedor acreditar que o checkout está de pé.
    const summary = stepSummaries(facts({ unitAmountMinor: 4990 })).oferta;
    expect(summary).toContain("Gratuito");
    expect(summary).toContain("49,90");
  });

  it("com vendas ativas, diz por quanto", () => {
    expect(stepSummaries(facts({ offerStatus: "active", unitAmountMinor: 4990 })).oferta)
      .toContain("Vendendo por");
  });

  it("respeita a moeda guardada da oferta", () => {
    const summary = stepSummaries(facts({ offerStatus: "active", unitAmountMinor: 1000, currency: "USD" })).oferta;
    expect(summary).not.toContain("R$");
  });

  it("vendas ativas sem preço não inventam um R$ 0,00", () => {
    // Par que não deveria existir — a ativação exige valor maior que zero —,
    // mas se o servidor devolver, a frase não pode anunciar um preço.
    const summary = stepSummaries(facts({ offerStatus: "active", unitAmountMinor: 0 })).oferta;
    expect(summary).toBe("Vendas ativas");
    expect(summary).not.toContain("0,00");
  });

  it("preço ausente é tratado como zero, não como falha", () => {
    expect(stepSummaries(facts({ unitAmountMinor: null })).oferta).toBe("Gratuito");
    expect(stepSummaries(facts({ unitAmountMinor: undefined })).oferta).toBe("Gratuito");
  });

  it("vendas encerradas são definitivas e aparecem como tal", () => {
    expect(stepSummaries(facts({ offerStatus: "archived" })).oferta).toBe("Vendas encerradas");
    // Mesmo com preço guardado: encerrada não vende mais, e mostrar o valor
    // sugeriria o contrário.
    expect(stepSummaries(facts({ offerStatus: "archived", unitAmountMinor: 4990 })).oferta)
      .toBe("Vendas encerradas");
  });
});

describe("resumo da distribuição", () => {
  it("um produto fora do ar diz isso, sem rodeio", () => {
    expect(stepSummaries(facts()).publicar).toBe("Ainda não está no ar");
  });

  it("no ar e sem pendência, diz desde quando", () => {
    expect(stepSummaries(facts({ published: true, publishedAt: "2026-08-25T12:00:00Z" })).publicar)
      .toBe("No ar desde 25 de agosto");
  });

  it("mudança por publicar tem precedência sobre a data", () => {
    // Saber que existe algo ainda não publicado importa mais do que saber
    // quando foi a última publicação.
    expect(stepSummaries(facts({ published: true, publishedAt: "2026-08-25T12:00:00Z", hasUnpublishedChanges: true })).publicar)
      .toBe("No ar, com mudanças por publicar");
  });

  it("no ar sem data conhecida ainda diz que está no ar", () => {
    expect(stepSummaries(facts({ published: true, publishedAt: null })).publicar).toBe("No ar");
  });
});

describe("publishedOnLabel", () => {
  it("escreve o mês por extenso", () => {
    expect(publishedOnLabel("2026-08-25T12:00:00Z")).toBe("25 de agosto");
  });

  it("não inventa data a partir de lixo", () => {
    expect(publishedOnLabel("ontem")).toBeNull();
    expect(publishedOnLabel(null)).toBeNull();
    expect(publishedOnLabel(undefined)).toBeNull();
    expect(publishedOnLabel("")).toBeNull();
  });
});
