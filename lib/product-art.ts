/**
 * A arte de um produto sem capa.
 *
 * Um produto sem imagem não pode virar retângulo vazio nem imagem quebrada: a
 * lista é a primeira impressão do catálogo. O gradiente vem do próprio nome, de
 * forma determinística, então o mesmo produto tem sempre a mesma cara — em
 * qualquer sessão, em qualquer máquina, sem guardar nada.
 */

const GOLDEN_ANGLE = 137.508;

/**
 * A faixa de matiz que a arte pode usar.
 *
 * Espalhar pelo círculo inteiro dava verde-limão, magenta e laranja ao lado de
 * uma interface azul-marinho — parecia defeito, não identidade. A arte precisa
 * distinguir um produto do outro, e para isso bastam os 150 graus que vão do
 * azul ao violeta passando pelo ciano: dois produtos ainda saem diferentes, e
 * nenhum sai gritando.
 */
const HUE_START = 190;
const HUE_SPAN = 150;

/** Hash estável e pequeno. Não é criptográfico e não precisa ser. */
function hashTitle(title: string): number {
  let hash = 2166136261;
  for (let index = 0; index < title.length; index += 1) {
    hash ^= title.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash);
}

export interface ProductArt {
  initials: string;
  gradient: string;
  hue: number;
}

export function productArt(title: string): ProductArt {
  const clean = title.trim();
  // O ângulo áureo espalha os matizes: dois produtos com nomes parecidos não
  // acabam com a mesma cor.
  const hue = HUE_START + Math.round((hashTitle(clean || "produto") * GOLDEN_ANGLE) % HUE_SPAN);
  const second = HUE_START + ((hue - HUE_START + 34) % HUE_SPAN);
  return {
    initials: initialsOf(clean),
    // Saturação contida e luminosidade baixa: a arte é fundo de uma miniatura,
    // não um cartaz. O texto branco por cima precisa continuar legível.
    gradient: `linear-gradient(135deg, hsl(${hue} 46% 32%), hsl(${second} 52% 22%))`,
    hue
  };
}

function initialsOf(title: string): string {
  const words = title.split(/\s+/).filter(Boolean);
  if (words.length === 0) return "—";
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase();
  return (words[0]![0]! + words[1]![0]!).toUpperCase();
}

/**
 * O preço que a lista pode mostrar.
 *
 * Devolve null quando não há preço em vigor. Uma oferta em rascunho pode ter
 * valor gravado, e mostrá-lo seria anunciar uma cobrança que ninguém pode
 * fazer ainda.
 */
export function catalogPrice(
  unitAmountMinor: number | null | undefined,
  currency: string | null | undefined
): string | null {
  if (typeof unitAmountMinor !== "number" || unitAmountMinor <= 0) return null;
  const code = (currency || "BRL").toUpperCase();
  try {
    return new Intl.NumberFormat("pt-BR", { style: "currency", currency: code })
      .format(unitAmountMinor / 100);
  } catch {
    return null;
  }
}

/**
 * Quantos alunos o produto tem, dito por extenso.
 *
 * "0 alunos" é uma frase honesta e a lista precisa dela: um produto novo tem
 * zero alunos, e esconder o número faria a coluna aparecer e sumir de card em
 * card. O que não pode acontecer é o número mentir, então um valor ausente ou
 * inválido vira zero em vez de virar "—", que sugere "não sabemos".
 */
export function studentCountLabel(students: number | null | undefined): string {
  const count = Number.isSafeInteger(students) && Number(students) > 0 ? Number(students) : 0;
  return count === 1 ? "1 aluno" : `${count.toLocaleString("pt-BR")} alunos`;
}
