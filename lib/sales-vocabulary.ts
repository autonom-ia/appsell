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
  { term: "espaço de vendas", instead: "sua loja" }
];

/**
 * Ainda não banidos, e por quê.
 *
 * `oferta` é rótulo de tela e conceito do domínio ao mesmo tempo: trocá-lo na
 * interface sem decidir o substituto deixaria a tela e a API falando línguas
 * diferentes sobre a mesma coisa. Fica registrado aqui, fora do portão, até a
 * decisão — e não esquecido num comentário solto.
 */
export const PENDING_SALES_TERMS: readonly BannedTerm[] = [
  { term: "oferta", instead: "decisão pendente: rótulo de tela x conceito do domínio" }
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
