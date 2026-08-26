// O estado de cada etapa do editor, dito por extenso.
//
// A navegação mostrava uma dica fixa ("Módulos e aulas") e uma bolinha. A
// bolinha não dizia se estava pronto, se faltava algo, nem o quê — e a palavra
// que dizia existia só para leitor de tela. Aqui ela passa a existir na tela.
//
// Nada disto é portão: continua sendo possível abrir qualquer etapa em qualquer
// ordem. Quem decide se dá para publicar é `detail.readiness`, do servidor.

import { formatOfferPrice } from "./offer-price";

export interface StepFacts {
  title: string;
  slug: string;
  modules: number;
  lessons: number;
  offerStatus: string;
  unitAmountMinor: number | null | undefined;
  currency: string | null | undefined;
  published: boolean;
  publishedAt?: string | null;
  hasUnpublishedChanges: boolean;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** "25 de agosto", no formato que se lê em voz alta. */
export function publishedOnLabel(publishedAt: string | null | undefined): string | null {
  if (!publishedAt) return null;
  const moment = Date.parse(publishedAt);
  if (!Number.isFinite(moment)) return null;
  return new Intl.DateTimeFormat("pt-BR", { day: "numeric", month: "long" }).format(new Date(moment));
}

/**
 * O resumo de uma etapa: o que ela tem, ou o que falta nela.
 *
 * Cada resposta é uma frase curta e concreta. "Pendente" não serve — foi
 * exatamente o tipo de rótulo que fez a bolinha não significar nada.
 */
export function stepSummaries(facts: StepFacts): Record<string, string> {
  return {
    produto: facts.title.trim() && facts.slug.trim()
      ? `@${facts.slug.trim()}`
      : "Falta o nome ou o endereço",
    curriculo: facts.modules > 0
      ? `${plural(facts.modules, "módulo", "módulos")}, ${plural(facts.lessons, "aula", "aulas")}`
      : "Nenhum módulo ainda",
    oferta: offerSummary(facts),
    publicar: publishSummary(facts)
  };
}

function offerSummary(facts: StepFacts): string {
  const amount = facts.unitAmountMinor ?? 0;
  if (facts.offerStatus === "archived") return "Vendas encerradas";
  if (facts.offerStatus === "active") return `Vendendo por ${formatOfferPrice(amount, facts.currency)}`;
  // Rascunho com preço salvo ainda não cobra nada: dizer o valor sem dizer que
  // as vendas estão desligadas faria o vendedor pensar que já está vendendo.
  if (amount > 0) return `Gratuito · preço de ${formatOfferPrice(amount, facts.currency)} salvo`;
  return "Gratuito";
}

function publishSummary(facts: StepFacts): string {
  if (!facts.published) return "Ainda não está no ar";
  if (facts.hasUnpublishedChanges) return "No ar, com mudanças por publicar";
  const on = publishedOnLabel(facts.publishedAt);
  return on ? `No ar desde ${on}` : "No ar";
}
