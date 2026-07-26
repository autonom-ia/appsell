export function transactionCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_oauth_tx" : "appsell_oauth_tx";
}

export function sessionCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_session" : "appsell_session";
}

export function csrfCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_csrf" : "appsell_csrf";
}

export function sellerCsrfCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_seller_csrf" : "appsell_seller_csrf";
}

export function productCsrfCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_product_csrf" : "appsell_product_csrf";
}

export function productUpdateCsrfCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_product_update_csrf" : "appsell_product_update_csrf";
}

export function productPublishCsrfCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_product_publish_csrf" : "appsell_product_publish_csrf";
}

export function demoInspectionCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_demo_inspection" : "appsell_demo_inspection";
}

// Marks that a recoverable callback failure already triggered one automatic
// login restart. Without it, a failure that survives the restart would bounce
// the browser between /auth/callback and /auth/login forever.
export function recoveryCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_oauth_recovery" : "appsell_oauth_recovery";
}

export function recoveryCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    ...sessionCookieOptions(production),
    maxAge: 120
  };
}

export function sessionCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    httpOnly: true as const,
    secure: production,
    sameSite: "lax" as const,
    path: "/" as const
  };
}

// Must stay in sync with the Redis transaction TTL: the cookie and the stored
// transaction are two halves of the same window. 5 minutes was shorter than a
// sign-up takes (fill the form, wait for the confirmation e-mail, confirm), so
// the default is 15 and the deployment can raise it without a code change.
// Bounded to keep `state`/PKCE from living arbitrarily long.
const DEFAULT_TRANSACTION_TTL_SECONDS = 15 * 60;
const MIN_TRANSACTION_TTL_SECONDS = 60;
const MAX_TRANSACTION_TTL_SECONDS = 60 * 60;

export function transactionTtlSeconds(
  environment: Partial<Record<string, string | undefined>> = process.env
): number {
  const configured = environment.APPSELL_OAUTH_TRANSACTION_TTL_SECONDS?.trim();
  // Unset or blank means "use the default"; Number("") would be 0 and clamp to
  // the minimum, silently shortening the window.
  if (!configured) return DEFAULT_TRANSACTION_TTL_SECONDS;
  const raw = Number(configured);
  if (!Number.isInteger(raw)) return DEFAULT_TRANSACTION_TTL_SECONDS;
  return Math.min(Math.max(raw, MIN_TRANSACTION_TTL_SECONDS), MAX_TRANSACTION_TTL_SECONDS);
}

export function transactionCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    ...sessionCookieOptions(production),
    maxAge: transactionTtlSeconds()
  };
}

export function csrfCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    httpOnly: false as const,
    secure: production,
    sameSite: "strict" as const,
    path: "/" as const
  };
}

export function demoInspectionCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    ...sessionCookieOptions(production),
    sameSite: "strict" as const,
    maxAge: 15 * 60
  };
}
