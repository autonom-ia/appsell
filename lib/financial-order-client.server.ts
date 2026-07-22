import "server-only";

import { financialInternalConfig, trimTrailingSlash } from "./financial-internal-config.server";

// Single fixed server-to-server GET against Financial's internal
// registration-checkout order status endpoint, used only by
// app/api/checkout/complete/route.ts to verify an order is paid before
// self-emitting the checkout-completion webhook. This intentionally reuses
// the same target path the registration-checkout proxy maps
// `/financial/me/checkout/:id` to (see mapRegistrationCheckoutPath in
// app/api/registration-checkout/[...path]/route.ts) but is called directly,
// server-side, without going through that browser-facing proxy route.

const FETCH_TIMEOUT_MS = 15_000;

export class FinancialOrderFetchError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export async function fetchRegistrationCheckoutOrder(orderId: string): Promise<unknown> {
  const config = financialInternalConfig();
  const targetUrl = new URL(
    `${trimTrailingSlash(config.apiUrl)}/financial/internal/registration-checkout/${encodeURIComponent(orderId)}`
  );
  // Chamada carrega x-api-key: exige HTTPS e nunca segue redirect (um 3xx
  // poderia desviar o segredo para outra origem). FINANCIAL_API_URL é env de
  // confiança, mas o hardening é barato e fail-closed.
  if (targetUrl.protocol !== "https:") {
    throw new FinancialOrderFetchError(503, "financial_order_insecure_endpoint");
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(targetUrl, {
      headers: { accept: "application/json", "x-api-key": config.apiKey },
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal
    });
    const payload = await response.json().catch(() => null) as unknown;
    if (!response.ok) {
      throw new FinancialOrderFetchError(response.status, "financial_order_fetch_failed");
    }
    return payload;
  } catch (error) {
    if (error instanceof FinancialOrderFetchError) throw error;
    throw new FinancialOrderFetchError(503, "financial_order_unavailable");
  } finally {
    clearTimeout(timeout);
  }
}
