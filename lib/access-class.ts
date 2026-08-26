// As escolhas de uma turma: quantas vagas e até quando.
//
// Existem como módulo próprio porque as duas viram valor de API e precisam ser
// testáveis sem tela. O prazo, principalmente: o campo era `datetime-local`,
// que pede hora e minuto para expirar um link. Ninguém decide uma turma assim
// — decide "sete dias", "trinta dias" ou uma data.

import { SEAT_RANGE } from "./numeric-field";

export const SEAT_PRESETS: readonly number[] = [10, 25, 50, 100];

export type SeatChoice = { kind: "preset"; seats: number } | { kind: "unlimited" } | { kind: "custom" };

export const EXPIRY_PRESET_DAYS: readonly number[] = [7, 30];

export type ExpiryChoice =
  | { kind: "days"; days: number }
  | { kind: "never" }
  | { kind: "date"; value: string };

const MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000;

/**
 * O instante em que a turma fecha, no formato que a rota espera. Vazio quer
 * dizer sem prazo — é uma escolha, não um campo por preencher.
 *
 * `now` entra por parâmetro em vez de ser lido aqui dentro: assim a conta é
 * pura, o teste não depende do relógio da máquina, e a renderização nunca lê a
 * hora (o painel já tem essa disciplina para julgar vencimento).
 */
export function expiresAtFrom(choice: ExpiryChoice, now: number): string {
  if (choice.kind === "never") return "";
  if (choice.kind === "days") return new Date(now + choice.days * MILLISECONDS_PER_DAY).toISOString();
  return endOfDay(choice.value);
}

/**
 * Uma data escolhida no calendário vale até o fim daquele dia.
 *
 * Interpretar "30 de setembro" como meia-noite do dia 30 fecharia a turma no
 * começo do dia que a pessoa quis incluir — o erro clássico de fim de intervalo,
 * e aqui ele tranca aluno do lado de fora.
 */
export function endOfDay(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return "";
  const [, year, month, day] = match;
  const local = new Date(Number(year), Number(month) - 1, Number(day), 23, 59, 59, 999);
  // Um dia impossível ("2026-02-31") rola para outro mês em vez de falhar.
  // Recusar é melhor do que gravar silenciosamente uma data que ninguém pediu.
  if (local.getMonth() !== Number(month) - 1 || local.getDate() !== Number(day)) return "";
  return local.toISOString();
}

/** O menor dia que ainda pode ser escolhido: hoje. */
export function earliestExpiryDate(now: number): string {
  const local = new Date(now);
  const month = String(local.getMonth() + 1).padStart(2, "0");
  const day = String(local.getDate()).padStart(2, "0");
  return `${local.getFullYear()}-${month}-${day}`;
}

export function isSeatPreset(value: string): boolean {
  return SEAT_PRESETS.some((preset) => String(preset) === value.trim());
}

/**
 * O valor que vai para `max_uses`. Vazio quer dizer sem limite — a rota já lê
 * assim, e um preset fora da faixa nunca deveria existir, mas é conferido
 * aqui porque a lista é código e código muda.
 */
export function maxUsesFrom(choice: SeatChoice, custom: string): string {
  if (choice.kind === "unlimited") return "";
  if (choice.kind === "custom") return custom.trim();
  const seats = choice.seats;
  return seats >= SEAT_RANGE.min && seats <= SEAT_RANGE.max ? String(seats) : "";
}
