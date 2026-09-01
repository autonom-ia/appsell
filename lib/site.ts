/** Identidade publica do Sell, em um lugar so. */
export const SITE = {
  origem: process.env.NEXT_PUBLIC_SITE_ORIGIN ?? "https://sell.autonomia.site",
  nome: "Autonom.ia Sell",
  organizacao: "Autonom.ia",
  taxaPercentual: 6.99,
  taxaFixa: 1.99
} as const;
