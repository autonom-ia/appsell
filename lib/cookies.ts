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

export function sessionCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    httpOnly: true as const,
    secure: production,
    sameSite: "lax" as const,
    path: "/" as const
  };
}

export function transactionCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    ...sessionCookieOptions(production),
    maxAge: 300
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
