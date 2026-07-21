// Conversão entre o input de preço do produtor (decimal pt-BR) e o valor
// canônico da oferta em centavos (offer.unit_amount_minor, migration 0018).

// Espelha o CHECK da coluna offer.unit_amount_minor e o teto da API.
export const MAX_UNIT_AMOUNT_MINOR = 99_999_999_999;

export type OfferBillingPeriod = "one_time" | "monthly" | "yearly";

export const OFFER_BILLING_PERIODS: ReadonlyArray<{ value: OfferBillingPeriod; label: string }> = [
  { value: "one_time", label: "Pagamento único" },
  { value: "monthly", label: "Assinatura mensal" },
  { value: "yearly", label: "Assinatura anual" }
];

const PRICE_INPUT = /^(\d{1,9})(?:[.,](\d{1,2}))?$/;

// "" -> 0 (oferta sem preço, permitido em rascunho). "12", "12,5", "12,50" e
// "12.50" -> centavos. Whitespace interno (inclusive NBSP/tab) é inválido —
// nunca normalizado silenciosamente: em dinheiro, "1 2,30" não pode virar 12,30.
export function parsePriceInputToMinor(input: string): number | null {
  const normalized = input.trim();
  if (normalized === "") return 0;
  if (/\s/.test(normalized)) return null;
  const match = PRICE_INPUT.exec(normalized);
  if (!match) return null;
  const cents = (match[2] ?? "").padEnd(2, "0");
  const value = Number(match[1]) * 100 + Number(cents);
  return value <= MAX_UNIT_AMOUNT_MINOR ? value : null;
}

// 0/undefined -> "" (input vazio); 150 -> "1,50".
export function priceInputFromMinor(unitAmountMinor: number | null | undefined): string {
  if (!unitAmountMinor || !Number.isInteger(unitAmountMinor) || unitAmountMinor <= 0) return "";
  const reais = Math.floor(unitAmountMinor / 100);
  const cents = unitAmountMinor % 100;
  return cents === 0 ? String(reais) : `${reais},${String(cents).padStart(2, "0")}`;
}

// Formata na moeda armazenada da oferta (pt-BR locale). Moeda inválida cai em
// BRL — nunca exibe um símbolo errado para um valor em outra moeda.
export function formatOfferPrice(unitAmountMinor: number, currency: string | null | undefined): string {
  const code = typeof currency === "string" && /^[A-Z]{3}$/.test(currency) ? currency : "BRL";
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: code })
    .format(unitAmountMinor / 100);
}

export function formatOfferPriceBRL(unitAmountMinor: number): string {
  return formatOfferPrice(unitAmountMinor, "BRL");
}

export function billingPeriodLabel(period: string | null | undefined): string {
  const found = OFFER_BILLING_PERIODS.find((entry) => entry.value === period);
  return found?.label ?? OFFER_BILLING_PERIODS[0]!.label;
}

export function isOfferBillingPeriod(value: string | null | undefined): value is OfferBillingPeriod {
  return OFFER_BILLING_PERIODS.some((entry) => entry.value === value);
}
