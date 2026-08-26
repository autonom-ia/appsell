import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { findBannedTerms, sourceProse } from "../lib/sales-vocabulary";

/**
 * O portão que não depende de a tela estar naquele estado.
 *
 * A varredura pelo navegador (tests/e2e/sales-vocabulary.spec.ts) só enxerga o
 * que o mock renderiza. Mensagem de sessão expirada, de conflito, de API fora
 * do ar — nada disso aparece num caminho feliz, e um termo banido podia morar
 * ali por anos. Foi exatamente o que aconteceu: "não tem acesso a este espaço"
 * passou pelo portão porque nenhum teste jamais provocou um 403.
 *
 * Este aqui lê o fonte. Para não acusar `payload.snapshot` nem o identificador
 * de área `"oferta"` — nomes de contrato, que ninguém lê na tela —, só olha o
 * que tem cara de frase: literal com espaço dentro, ou texto entre tags.
 */

const SELLER_SURFACES = [
  "app/components",
  "app/(protected)/products",
  "app/(protected)/payouts",
  "app/(protected)/dashboard"
];

function tsxFilesIn(directory: string): string[] {
  let entries;
  try {
    entries = readdirSync(directory, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries.flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return tsxFilesIn(path);
    return entry.name.endsWith(".tsx") ? [path] : [];
  });
}

const files = SELLER_SURFACES.flatMap((surface) => tsxFilesIn(surface));

describe("B3.2 — o portão de vocabulário lê o fonte, não só o caminho feliz", () => {
  it("encontra as telas do vendedor", () => {
    // Um portão que não achou arquivo nenhum é um portão aberto que parece
    // fechado — a mesma armadilha da suíte que "passa" com 318 pulados.
    expect(files.length).toBeGreaterThan(10);
  });

  it.each(files)("%s não diz nenhum termo de sistema", (file) => {
    const hits = findBannedTerms(sourceProse(readFileSync(file, "utf8")));
    const report = hits
      .map((hit) => `${file}:${hit.line} — "${hit.term}" (diga "${hit.instead}"): ${hit.excerpt}`)
      .join("\n");

    expect(report).toBe("");
  });
});

describe("sourceProse", () => {
  it("pega a frase e ignora o identificador", () => {
    const source = [
      'const section = "oferta";',
      'const message = "A oferta mudou em outra sessão.";'
    ].join("\n");

    const hits = findBannedTerms(sourceProse(source));

    // `"oferta"` sozinho é o valor que vai na URL: contrato, não texto de tela.
    expect(hits).toHaveLength(1);
    expect(hits[0]?.line).toBe(2);
  });

  it("pega texto solto entre tags", () => {
    const hits = findBannedTerms(sourceProse("<h2>Estruture sua próxima oferta</h2>"));

    expect(hits.map((hit) => hit.term)).toEqual(["oferta"]);
  });

  it("não acusa nome de campo da API", () => {
    expect(findBannedTerms(sourceProse("return payload.snapshot.course;"))).toHaveLength(0);
  });

  it("não acusa comentário que cita o termo para explicar a remoção", () => {
    const source = [
      '// Nem "Espaço selecionado" nem os oito primeiros caracteres do UUID.',
      "/* O currículo virou Conteúdo aqui. */",
      'const label = "Loja";'
    ].join("\n");

    expect(findBannedTerms(sourceProse(source))).toHaveLength(0);
  });

  it("não engole a linha ao apagar um comentário de bloco", () => {
    // O apagador troca cada caractere do bloco por espaço em vez de removê-lo,
    // senão as linhas seguintes escorregariam e o número reportado apontaria
    // para a linha errada.
    const source = ["/* comentário", "de duas linhas */", 'const m = "diga oferta aqui";'].join("\n");

    expect(findBannedTerms(sourceProse(source))[0]?.line).toBe(3);
  });
});
