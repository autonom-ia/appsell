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

export function demoInspectionCookieOptions(production = process.env.NODE_ENV === "production") {
  return {
    ...sessionCookieOptions(production),
    sameSite: "strict" as const,
    maxAge: 15 * 60
  };
}
