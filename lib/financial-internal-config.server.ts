import "server-only";

// Shared config for the two server-side callers of Financial's internal
// (x-api-key) surface: app/api/registration-checkout/[...path]/route.ts
// (generic passthrough proxy for the browser) and
// lib/financial-order-client.server.ts (a single fixed GET this BFF makes on
// its own, never reachable from the browser). Both must agree on the same
// base URL/credential; this module is the one place that reads them.

export class FinancialInternalConfigError extends Error {}

export interface FinancialInternalConfig {
  apiUrl: string;
  apiKey: string;
}

export function financialInternalConfig(): FinancialInternalConfig {
  const apiUrl = process.env.FINANCIAL_API_URL ?? "https://financial.api-autonomia.com";
  const apiKey = process.env.FINANCIAL_INTERNAL_API_KEY ?? "";
  if (!apiKey) throw new FinancialInternalConfigError("financial_internal_not_configured");
  return { apiUrl, apiKey };
}

export function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/g, "");
}
