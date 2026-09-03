import { describe, expect, it } from "vitest";

import { catalogPrice, productArt } from "../lib/product-art";
import { BANNED_SALES_TERMS, findBannedTerms } from "../lib/sales-vocabulary";

describe("A4.2 — produto sem capa nunca fica vazio", () => {
  it("dá a mesma arte ao mesmo nome, sempre", () => {
    const first = productArt("IA na prática");
    const second = productArt("IA na prática");

    expect(first).toEqual(second);
    expect(first.gradient).toMatch(/^linear-gradient\(135deg, hsl\(\d+ 46% 32%\), hsl\(\d+ 52% 22%\)\)$/);
  });

  it("separa nomes parecidos em cores diferentes", () => {
    const hues = ["Curso A", "Curso B", "Curso C", "Curso D"].map((name) => productArt(name).hue);

    expect(new Set(hues).size).toBe(hues.length);
  });

  it("fica dentro da faixa da marca, sem verde-limão nem laranja", () => {
    // Espalhar pelo círculo inteiro punha magenta e laranja ao lado de uma
    // interface azul-marinho, e o resultado parecia defeito. A distinção entre
    // produtos continua; o grito, não.
    const nomes = ["IA na prática", "Codex do zero", "Vendas com IA", "Automação real",
                   "Dados para todos", "Prompt avançado", "", "x"];

    for (const nome of nomes) {
      const { hue } = productArt(nome);
      expect(hue).toBeGreaterThanOrEqual(190);
      expect(hue).toBeLessThan(340);
    }
  });

  it("sempre produz iniciais legíveis, inclusive no limite", () => {
    expect(productArt("IA na prática").initials).toBe("IN");
    expect(productArt("Codex").initials).toBe("CO");
    expect(productArt("  ").initials).toBe("·");
    expect(productArt("").initials).toBe("·");
  });

  it("nunca devolve gradiente vazio", () => {
    for (const name of ["", "  ", "x", "Nome bem comprido de produto para testar"]) {
      expect(productArt(name).gradient.length).toBeGreaterThan(30);
    }
  });
});

describe("A4.4 — preço só quando há preço em vigor", () => {
  it("formata o valor de uma oferta ativa", () => {
    expect(catalogPrice(4990, "BRL")).toContain("49,90");
  });

  it("não inventa preço quando não há valor", () => {
    // O servidor já devolve null para oferta que não está ativa. Esta é a
    // segunda barreira: mesmo que um valor escape, zero e nulo não viram preço.
    expect(catalogPrice(null, "BRL")).toBeNull();
    expect(catalogPrice(undefined, "BRL")).toBeNull();
    expect(catalogPrice(0, "BRL")).toBeNull();
    expect(catalogPrice(-100, "BRL")).toBeNull();
  });

  it("não quebra com uma moeda que não existe", () => {
    expect(catalogPrice(4990, "XYZ_INVALIDA")).toBeNull();
  });
});

describe("A4.5 — varredura de vocabulário", () => {
  it("acha o termo ignorando acento e caixa", () => {
    const hits = findBannedTerms("Publique o SNAPSHOT agora\nUse o Espaco De Vendas");

    expect(hits.map((hit) => hit.term).sort()).toEqual(["espaço", "snapshot"]);
  });

  // O furo que a #255 fechou: o termo banido era a frase inteira "espaço de
  // vendas", e a tela dizia ESPAÇO SELECIONADO — passava batido. O portão pega
  // a ideia agora, não a frase.
  it("pega espaço fora da frase que estava banida", () => {
    const hits = findBannedTerms("ESPAÇO SELECIONADO · AEA20A25…");

    expect(hits.map((hit) => hit.term)).toEqual(["espaço"]);
  });

  it("aponta a linha e sugere o que dizer no lugar", () => {
    const [hit] = findBannedTerms("linha limpa\nesta tem snapshot");

    expect(hit?.line).toBe(2);
    expect(hit?.instead).toBe("versão publicada");
  });

  it("fica calada quando o texto está limpo", () => {
    expect(findBannedTerms("Publique a versão. Use sua loja.")).toEqual([]);
  });

  it("mantém a lista de termos não vazia", () => {
    expect(BANNED_SALES_TERMS.length).toBeGreaterThan(0);
  });
});
