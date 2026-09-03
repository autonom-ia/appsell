/**
 * A marca da loja, do lado de quem a mostra.
 *
 * A cor de destaque do vendedor só é usada onde há texto branco em cima —
 * botão de compra, fundo da identidade. Por isso a régua é uma só: contraste
 * de pelo menos 4,5:1 contra branco (WCAG AA para texto normal). Duas
 * defesas usam a mesma conta: o BFF recusa ao gravar, e a página recalcula ao
 * pintar. Se por qualquer caminho uma cor ilegível chegar ao banco, a página
 * cai no tom da Autonom.ia em vez de renderizar texto invisível.
 *
 * Sem I/O e sem DOM de propósito: o teste prova a conta sem montar nada.
 */

export const MIN_ACCENT_CONTRAST = 4.5;
const HEX_COLOR = /^#[0-9a-f]{6}$/;
const WHITE = "#ffffff";

/** Normaliza o que veio do formulário para o formato que o banco aceita. */
export function normalizeAccentColor(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim().toLowerCase();
  if (trimmed === "") return null;
  const withHash = trimmed.startsWith("#") ? trimmed : `#${trimmed}`;
  return HEX_COLOR.test(withHash) ? withHash : null;
}

export function isHexColor(value: unknown): value is string {
  return typeof value === "string" && HEX_COLOR.test(value);
}

/** Luminância relativa (WCAG 2.x), de 0 (preto) a 1 (branco). */
export function relativeLuminance(hex: string): number {
  const channel = (index: number) => {
    const raw = Number.parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16) / 255;
    return raw <= 0.03928 ? raw / 12.92 : ((raw + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

export function contrastRatio(foreground: string, background: string): number {
  const first = relativeLuminance(foreground);
  const second = relativeLuminance(background);
  const [light, dark] = first >= second ? [first, second] : [second, first];
  return (light + 0.05) / (dark + 0.05);
}

/** A cor serve de fundo para texto branco? */
export function isAccentReadable(hex: string): boolean {
  return isHexColor(hex) && contrastRatio(hex, WHITE) >= MIN_ACCENT_CONTRAST;
}

/**
 * A cor que a página pode usar, ou nada.
 *
 * Nada quer dizer "tom da Autonom.ia": a página não põe a variável de CSS e o
 * estilo padrão vale. É a guarda de renderização do critério de aceite.
 */
export function storeAccent(value: unknown): string | null {
  return isHexColor(value) && isAccentReadable(value) ? value : null;
}

/** As iniciais que a vitrine mostra enquanto a loja não tem logo. */
export function storeInitials(name: string): string {
  const parts = name.split(/\s+/u).filter(Boolean);
  if (parts.length === 0) return "·";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return `${parts[0]![0]!}${parts[1]![0]!}`.toUpperCase();
}

/** Mensagem para quem escolheu uma cor que não passa. */
export function accentRejectionMessage(hex: string): string {
  const ratio = isHexColor(hex) ? contrastRatio(hex, WHITE) : 0;
  return `Essa cor não contrasta o bastante com o texto branco dos botões (${ratio.toFixed(1)}:1; o mínimo é ${MIN_ACCENT_CONTRAST}:1). Escolha um tom mais escuro.`;
}
