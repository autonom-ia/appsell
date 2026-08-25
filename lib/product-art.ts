/**
 * A arte de um produto sem capa.
 *
 * Um produto sem imagem não pode virar retângulo vazio nem imagem quebrada: a
 * lista é a primeira impressão do catálogo. O gradiente vem do próprio nome, de
 * forma determinística, então o mesmo produto tem sempre a mesma cara — em
 * qualquer sessão, em qualquer máquina, sem guardar nada.
 */

const GOLDEN_ANGLE = 137.508;

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
  const hue = Math.round((hashTitle(clean || "produto") * GOLDEN_ANGLE) % 360);
  const second = (hue + 38) % 360;
  return {
    initials: initialsOf(clean),
    gradient: `linear-gradient(135deg, hsl(${hue} 62% 42%), hsl(${second} 58% 32%))`,
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
