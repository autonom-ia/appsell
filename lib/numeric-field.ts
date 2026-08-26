// Domínio dos campos numéricos da interface do vendedor.
//
// A regra que este módulo existe para cumprir: um campo numérico não aceita o
// que o servidor vai recusar. Antes disto, o campo Vagas era `type="text"` e
// engolia "dez" sem reclamar — o vendedor só descobria no envio, com um
// "revise o campo destacado" que não dizia qual era o limite.
//
// Os limites aqui espelham os que a API aplica. Quando divergirem, quem manda é
// a API; o papel deste módulo é antecipar a recusa, nunca substituí-la.

// Espelha `max_uses: int | None = Field(ge=1, le=100_000)` em catalog_routes.py
// e `parseSeats` na rota do BFF.
export const SEAT_RANGE = { min: 1, max: 100_000 } as const;

// Espelha PRICE_INPUT em lib/offer-price.ts: até 9 dígitos inteiros e 2 casas.
const PRICE_INTEGER_DIGITS = 9;
const PRICE_DECIMAL_DIGITS = 2;

export type SeatReading =
  | { kind: "empty" }
  | { kind: "ok"; seats: number }
  | { kind: "below" }
  | { kind: "above" };

// Vazio significa ilimitado — é um valor legítimo, não um campo por preencher.
export function readSeatInput(raw: string): SeatReading {
  const trimmed = raw.trim();
  if (trimmed === "") return { kind: "empty" };
  // O filtro de digitação já impede letras; um resto não numérico aqui só
  // aparece por colagem, e vale a mesma resposta de fora de faixa.
  if (!/^\d+$/.test(trimmed)) return { kind: "below" };
  const seats = Number(trimmed);
  if (seats < SEAT_RANGE.min) return { kind: "below" };
  if (seats > SEAT_RANGE.max) return { kind: "above" };
  return { kind: "ok", seats };
}

const SEAT_LIMIT_TEXT = `${SEAT_RANGE.min} a ${SEAT_RANGE.max.toLocaleString("pt-BR")}`;

// O texto de ajuda fica sempre visível: o limite é informação do campo, não
// castigo por errar.
export const SEAT_HELP = `De ${SEAT_LIMIT_TEXT} vagas. Deixe vazio para ilimitado.`;

export function seatRangeMessage(reading: SeatReading): string | null {
  if (reading.kind === "below") return `A menor turma tem ${SEAT_RANGE.min} vaga. Use de ${SEAT_LIMIT_TEXT}.`;
  if (reading.kind === "above") return `O limite por link é ${SEAT_RANGE.max.toLocaleString("pt-BR")} vagas.`;
  return null;
}

// Descarta tudo que não for dígito. Não normaliza nem completa nada: apenas
// impede que um caractere impossível chegue ao campo.
export function keepDigits(raw: string): string {
  return raw.replace(/\D/g, "").slice(0, String(SEAT_RANGE.max).length);
}

/**
 * Mantém apenas o que pode compor um preço em pt-BR: dígitos e um único
 * separador decimal. Não troca vírgula por ponto nem preenche centavos — em
 * dinheiro, corrigir por conta própria o que a pessoa digitou é pior do que
 * recusar.
 */
export function keepPriceCharacters(raw: string): string {
  const cleaned = raw.replace(/[^\d.,]/g, "");
  const separator = cleaned.search(/[.,]/);
  if (separator === -1) return cleaned.slice(0, PRICE_INTEGER_DIGITS);
  const whole = cleaned.slice(0, separator).slice(0, PRICE_INTEGER_DIGITS);
  const rest = cleaned.slice(separator + 1).replace(/[.,]/g, "").slice(0, PRICE_DECIMAL_DIGITS);
  return `${whole}${cleaned[separator]}${rest}`;
}

// `type="number"` deixa passar notação científica e sinal, e o valor chega
// vazio ao React quando isso acontece — o campo parece apagar sozinho. Barrar a
// tecla é o que impede o estado impossível.
const REJECTED_NUMERIC_KEYS = new Set(["e", "E", "+", "-", ",", "."]);

export function isRejectedNumericKey(key: string): boolean {
  return REJECTED_NUMERIC_KEYS.has(key);
}
