import { createHash } from "node:crypto";

// #440: the store is the seller. Stores on APPSELL_FINANCIAL_CONNECT_SELLER_IDS
// (comma-separated seller ids, empty by default) send their own connected
// account and the platform commission to the Financial checkout. Every other
// store keeps today's payload, with no account and no commission.
export function isFinancialConnectSeller(
  sellerId: string,
  environment: Partial<Record<string, string | undefined>> = process.env
): boolean {
  const listed = (environment.APPSELL_FINANCIAL_CONNECT_SELLER_IDS ?? "")
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter((entry) => entry.length > 0);
  return listed.includes(sellerId.toLowerCase());
}

// The same click repeated inside one window reaches the same Financial order
// (startExternalCheckout returns the existing order for a known key, and the
// hosted session is reused per order). The window bounds how long a stale
// order can be reused; a new price, account or commission is a new key.
export const CONNECT_IDEMPOTENCY_WINDOW_MS = 30 * 60 * 1000;
const CONNECT_IDEMPOTENCY_PREFIX = "sell-connect-v1:";

export interface ConnectCheckoutKeyParts {
  sellerId: string;
  productId: string;
  offerId: string;
  buyerIdentitySub: string;
  amountMinor: number;
  accountId: string;
  applicationFeeAmountMinor: number;
}

// Hashed, so the buyer's subject never travels in clear in the key.
export function connectCheckoutIdempotencyKey(parts: ConnectCheckoutKeyParts, nowMs: number = Date.now()): string {
  const windowIndex = Math.floor(nowMs / CONNECT_IDEMPOTENCY_WINDOW_MS);
  const material = JSON.stringify([
    parts.sellerId,
    parts.productId,
    parts.offerId,
    parts.buyerIdentitySub,
    parts.amountMinor,
    parts.accountId,
    parts.applicationFeeAmountMinor,
    windowIndex
  ]);
  return `${CONNECT_IDEMPOTENCY_PREFIX}${createHash("sha256").update(material).digest("hex")}`;
}
