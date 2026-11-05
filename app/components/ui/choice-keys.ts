/**
 * Modelo de teclado do padrão WAI-ARIA "select-only combobox", usado por toda
 * escolha única do Sell (`ChoiceSelect`). Fica puro para ser testado sem DOM;
 * o componente só traduz a ação devolvida em estado.
 */
export type ChoiceKeyInput = {
  key: string;
  altKey?: boolean;
  open: boolean;
  active: number;
  selected: number;
  count: number;
};

export type ChoiceKeyAction =
  | { type: "none" }
  | { type: "open"; active: number }
  | { type: "move"; active: number }
  | { type: "commit"; active: number; keepDefault: boolean }
  | { type: "close" };

const PAGE_SIZE = 10;

function clamp(value: number, count: number): number {
  return Math.min(Math.max(value, 0), Math.max(count - 1, 0));
}

function closedAction(key: string, start: number, count: number): ChoiceKeyAction {
  if (key === "ArrowDown" || key === "ArrowUp" || key === "Enter" || key === " ") return { type: "open", active: start };
  if (key === "Home") return { type: "open", active: 0 };
  if (key === "End") return { type: "open", active: count - 1 };
  return { type: "none" };
}

export function choiceKeyAction({ key, altKey = false, open, active, selected, count }: ChoiceKeyInput): ChoiceKeyAction {
  if (count === 0) return { type: "none" };
  const start = selected >= 0 ? selected : 0;
  if (!open) return closedAction(key, start, count);
  const current = active >= 0 ? active : start;
  if (key === "ArrowUp" && altKey) return { type: "commit", active: current, keepDefault: false };
  if (key === "Enter" || key === " ") return { type: "commit", active: current, keepDefault: false };
  // Tab escolhe e deixa o foco seguir para o próximo campo.
  if (key === "Tab") return { type: "commit", active: current, keepDefault: true };
  if (key === "Escape") return { type: "close" };
  if (key === "ArrowDown") return { type: "move", active: clamp(current + 1, count) };
  if (key === "ArrowUp") return { type: "move", active: clamp(current - 1, count) };
  if (key === "PageDown") return { type: "move", active: clamp(current + PAGE_SIZE, count) };
  if (key === "PageUp") return { type: "move", active: clamp(current - PAGE_SIZE, count) };
  if (key === "Home") return { type: "move", active: 0 };
  if (key === "End") return { type: "move", active: count - 1 };
  return { type: "none" };
}

const COMBINING_MARKS_START = 0x0300;
const COMBINING_MARKS_END = 0x036f;

/** Minúsculas e sem acento, para "Únic" achar "único". Sem regex (regra do projeto). */
export function foldForTypeahead(text: string): string {
  return Array.from(text.normalize("NFD"))
    .filter((char) => {
      const code = char.codePointAt(0) ?? 0;
      return code < COMBINING_MARKS_START || code > COMBINING_MARKS_END;
    })
    .join("")
    .toLowerCase();
}

/**
 * Busca por prefixo a partir da opção depois de `from`. Repetir a mesma letra
 * alterna entre as opções que começam com ela, como no controle nativo.
 */
export function typeaheadIndex(labels: readonly string[], query: string, from: number): number {
  const folded = foldForTypeahead(query);
  if (!folded || labels.length === 0) return -1;
  const chars = Array.from(folded);
  const repeated = chars.length > 1 && chars.every((char) => char === chars[0]);
  const needle = repeated ? chars[0]! : folded;
  const offset = repeated || chars.length === 1 ? 1 : 0;
  for (let step = 0; step < labels.length; step += 1) {
    const index = (Math.max(from, -1) + offset + step + labels.length) % labels.length;
    if (foldForTypeahead(labels[index]!).startsWith(needle)) return index;
  }
  return -1;
}
