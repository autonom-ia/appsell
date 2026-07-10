export function transactionCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_oauth_tx" : "appsell_oauth_tx";
}

export function sessionCookieName(production = process.env.NODE_ENV === "production") {
  return production ? "__Host-appsell_session" : "appsell_session";
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
