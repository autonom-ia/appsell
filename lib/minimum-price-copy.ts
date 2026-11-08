import { formatOfferPriceBRL } from "./offer-price";

// Texto do preço mínimo para o produtor (#546). Fica fora de minimum-price.ts
// porque aquele arquivo puxa a tabela de planos, que é só do servidor; este
// roda no editor. Sem jargão: o produtor precisa saber o que fazer.
export function minimumPriceMessage(minimumMinor: number): string {
  return `Com esse preço, a taxa da plataforma fica com todo o valor e a loja não recebe nada. O menor preço possível é ${formatOfferPriceBRL(minimumMinor)}.`;
}

// Lê o mínimo da resposta 422 das rotas de preço, status e publicação.
export function minimumFromResponse(payload: unknown): number | null {
  if (typeof payload !== "object" || payload === null) return null;
  const record = payload as Record<string, unknown>;
  const minimum = record.minimum_minor;
  return record.reason === "below_minimum" && typeof minimum === "number" && Number.isSafeInteger(minimum) && minimum > 0
    ? minimum
    : null;
}
