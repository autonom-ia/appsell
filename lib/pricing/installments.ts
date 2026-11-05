// Conta das parcelas do checkout (#556). Só o cálculo: quem cobra é o gateway
// Brasil (#547), e o preço que entra aqui é o pinado no pedido (#154).
//
// Regra: arredonda o TOTAL para o centavo antes de dividir, e o resto da
// divisão vai na primeira parcela. A soma das parcelas é sempre o total.
//
// Juros do comprador seguem a Tabela Price (parcela fixa). A conta é feita em
// frações exatas com BigInt: nenhum ponto flutuante decide centavo.

import { formatOfferPrice, MAX_UNIT_AMOUNT_MINOR } from "../offer-price";

export const MAX_INSTALLMENTS = 12;
/** 100% ao mês. Acima disso é erro de configuração, não taxa real. */
export const MAX_MONTHLY_RATE_BASIS_POINTS = 10_000;
const BASIS = 10_000n;

export type InterestPayer = "buyer" | "seller";

export type InstallmentInput = {
  principalMinor: number;
  count: number;
  /** Juros ao mês em pontos-base: 199 = 1,99% a.m. */
  monthlyRateBasisPoints: number;
  interestPayer: InterestPayer;
};

export type InstallmentPlan = {
  count: number;
  installmentsMinor: readonly number[];
  totalMinor: number;
  principalMinor: number;
  /** O que o comprador paga a mais que o preço. Zero quando a loja paga os juros. */
  interestMinor: number;
  interestPayer: InterestPayer;
  monthlyRateBasisPoints: number;
};

export class InstallmentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InstallmentError";
  }
}

function requireInteger(value: number, min: number, max: number, field: string): void {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    throw new InstallmentError(`${field} fora do intervalo ${min}..${max}`);
  }
}

function validate(input: InstallmentInput): void {
  requireInteger(input.principalMinor, 1, MAX_UNIT_AMOUNT_MINOR, "principalMinor");
  requireInteger(input.count, 1, MAX_INSTALLMENTS, "count");
  requireInteger(input.monthlyRateBasisPoints, 0, MAX_MONTHLY_RATE_BASIS_POINTS, "monthlyRateBasisPoints");
  if (input.interestPayer !== "buyer" && input.interestPayer !== "seller") {
    throw new InstallmentError("interestPayer deve ser buyer ou seller");
  }
  if (input.count > input.principalMinor) {
    throw new InstallmentError("cada parcela precisa de pelo menos um centavo");
  }
}

/** Divisão inteira arredondando meio para cima (valores positivos). */
function roundHalfUp(numerator: bigint, denominator: bigint): bigint {
  return (2n * numerator + denominator) / (2n * denominator);
}

/**
 * Total da Tabela Price: n · P · i · (1+i)^n / ((1+i)^n − 1), com i = bp/10000.
 * Multiplicando em cima e embaixo por 10000^n, tudo fica inteiro:
 * n · P · bp · (10000+bp)^n / (10000 · ((10000+bp)^n − 10000^n)).
 */
function priceTableTotalMinor(principalMinor: number, count: number, rateBasisPoints: number): bigint {
  const n = BigInt(count);
  const bp = BigInt(rateBasisPoints);
  const grown = (BASIS + bp) ** n;
  const numerator = n * BigInt(principalMinor) * bp * grown;
  const denominator = BASIS * (grown - BASIS ** n);
  return roundHalfUp(numerator, denominator);
}

function buyerTotalMinor(input: InstallmentInput): number {
  const chargesInterest = input.interestPayer === "buyer" && input.count > 1 && input.monthlyRateBasisPoints > 0;
  if (!chargesInterest) return input.principalMinor;
  const total = priceTableTotalMinor(input.principalMinor, input.count, input.monthlyRateBasisPoints);
  // O total com juros respeita o mesmo teto do preço da oferta.
  if (total > BigInt(MAX_UNIT_AMOUNT_MINOR)) {
    throw new InstallmentError("total com juros acima do teto da oferta");
  }
  return Number(total);
}

function splitWithRemainderFirst(totalMinor: number, count: number): number[] {
  const base = Math.floor(totalMinor / count);
  const remainder = totalMinor - base * count;
  return Array.from({ length: count }, (_, index) => (index === 0 ? base + remainder : base));
}

export function installmentPlan(input: InstallmentInput): InstallmentPlan {
  validate(input);
  const totalMinor = buyerTotalMinor(input);
  return Object.freeze({
    count: input.count,
    installmentsMinor: Object.freeze(splitWithRemainderFirst(totalMinor, input.count)),
    totalMinor,
    principalMinor: input.principalMinor,
    interestMinor: totalMinor - input.principalMinor,
    interestPayer: input.interestPayer,
    monthlyRateBasisPoints: input.monthlyRateBasisPoints
  });
}

export type InstallmentOptionsInput = Omit<InstallmentInput, "count"> & {
  maxCount: number;
  /** Parcela mínima aceita pelo gateway. À vista aparece sempre. */
  minInstallmentMinor: number;
};

export function installmentOptions(input: InstallmentOptionsInput): InstallmentPlan[] {
  requireInteger(input.maxCount, 1, MAX_INSTALLMENTS, "maxCount");
  requireInteger(input.minInstallmentMinor, 0, MAX_UNIT_AMOUNT_MINOR, "minInstallmentMinor");
  const { maxCount, minInstallmentMinor, ...rest } = input;
  const plans = [installmentPlan({ ...rest, count: 1 })];
  for (let count = 2; count <= Math.min(maxCount, input.principalMinor); count += 1) {
    const plan = installmentPlan({ ...rest, count });
    if (Math.min(...plan.installmentsMinor) < minInstallmentMinor) break;
    plans.push(plan);
  }
  return plans;
}

function formatRate(basisPoints: number): string {
  const whole = Math.floor(basisPoints / 100);
  const cents = basisPoints % 100;
  return `${whole},${String(cents).padStart(2, "0")}% a.m.`;
}

const CURRENCY_CODE_LENGTH = 3;

/** Código ISO 4217 em maiúsculas. Sem regex: confere caractere a caractere. */
function isCurrencyCode(value: unknown): value is string {
  return typeof value === "string"
    && value.length === CURRENCY_CODE_LENGTH
    && Array.from(value).every((char) => char >= "A" && char <= "Z");
}

/**
 * Texto para o checkout: o valor de cada parcela, o total e quem paga os juros.
 * Moeda inválida é erro: aqui nunca cai em BRL, para o comprador não ver um
 * símbolo de moeda que não é a do pedido.
 */
export function installmentSummary(plan: InstallmentPlan, currency: string): string {
  if (!isCurrencyCode(currency)) throw new InstallmentError("moeda inválida");
  const money = (minor: number) => formatOfferPrice(minor, currency);
  const total = money(plan.totalMinor);
  if (plan.count === 1) return `à vista ${total}`;
  const [first, second] = plan.installmentsMinor as readonly [number, number];
  const parts = first === second
    ? `${plan.count}x de ${money(first)}`
    : `1ª de ${money(first)} + ${plan.count - 1}x de ${money(second)}`;
  if (plan.interestMinor === 0) return `${parts} sem juros · total ${total}`;
  return `${parts} com juros de ${formatRate(plan.monthlyRateBasisPoints)} · total ${total} (${money(plan.interestMinor)} de juros)`;
}
