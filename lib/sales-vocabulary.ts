/**
 * Termos de sistema que não podem aparecer na área de vendas (A4.5).
 *
 * A lista não é de estilo, é de significado: cada um destes descreve como o
 * produto funciona por dentro, e quem está vendendo um curso não deveria
 * precisar aprender o vocabulário da nossa implementação para usar a tela.
 *
 * O portão existe para o termo não voltar sem que alguém decida que ele volta.
 */

export interface BannedTerm {
  /** Como o termo aparece hoje. A busca ignora maiúsculas e acentos. */
  term: string;
  /** O que dizer no lugar, para quem for corrigir. */
  instead: string;
}

export const BANNED_SALES_TERMS: readonly BannedTerm[] = [
  { term: "snapshot", instead: "versão publicada" },
  // "espaço", não "espaço de vendas": a tela dizia ESPAÇO SELECIONADO e passava
  // pelo portão porque a frase banida era outra. O portão pega a ideia agora.
  // Onde "espaço" queria dizer o branco entre palavras, o texto foi reescrito
  // para não depender do substantivo.
  { term: "espaço", instead: "loja" },
  { term: "currículo", instead: "conteúdo" },
  // Decidido: na tela do vendedor a etapa se chama Preço, e o que se liga e
  // desliga são as vendas. "Oferta" continua sendo o nome da coisa na API e no
  // banco — o contrato não muda, só para de vazar para quem vende.
  { term: "oferta", instead: "preço, ou vendas" }
];

function fold(value: string): string {
  return value.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

export interface VocabularyHit {
  term: string;
  instead: string;
  line: number;
  excerpt: string;
}

/** Onde cada termo banido aparece num arquivo de texto. */
export function findBannedTerms(
  source: string,
  terms: readonly BannedTerm[] = BANNED_SALES_TERMS
): VocabularyHit[] {
  const hits: VocabularyHit[] = [];
  const lines = source.split("\n");
  for (const [index, line] of lines.entries()) {
    const folded = fold(line);
    for (const banned of terms) {
      if (!folded.includes(fold(banned.term))) continue;
      hits.push({
        term: banned.term,
        instead: banned.instead,
        line: index + 1,
        excerpt: line.trim().slice(0, 120)
      });
    }
  }
  return hits;
}

/**
 * Extrai de um arquivo-fonte só o que vira frase na tela, preservando o número
 * da linha (as demais saem vazias).
 *
 * Existe porque a varredura pelo navegador só enxerga o que o mock renderiza:
 * uma mensagem de erro, de conflito ou de sessão expirada pode carregar um
 * termo banido por anos sem nunca aparecer num teste de caminho feliz. Foi
 * assim que "não tem acesso a este espaço" sobreviveu ao portão.
 *
 * A regra do que é frase: literal entre aspas duplas **com espaço dentro**, ou
 * texto solto entre tags. Isso deixa de fora justamente o que não é para ser
 * lido — `"oferta"` como identificador de área, `payload.snapshot` como nome de
 * campo da API — sem precisar de lista de exceção.
 */
export function sourceProse(source: string): string {
  return stripComments(source)
    .split("\n")
    .map((line) => {
      const quoted = [...line.matchAll(/"([^"\\\n]{2,})"/g)]
        .map((match) => match[1]!)
        .filter((literal) => literal.includes(" "));
      const jsxText = [...line.matchAll(/>([^<>{}\n]*[a-zA-ZÀ-ÿ][^<>{}\n]*)</g)]
        .map((match) => match[1]!.trim())
        .filter(Boolean);
      return [...quoted, ...jsxText].join(" ");
    })
    .join("\n");
}

// Comentário não é tela. Um comentário que cita o termo para explicar por que
// ele saiu não pode reprovar o portão.
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, prefix: string) =>
      prefix + " ".repeat(match.length - prefix.length));
}
