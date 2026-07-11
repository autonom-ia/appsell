export class PublicOriginConfigurationError extends Error {}

export function configuredPublicOrigin(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  const raw = environment.APPSELL_PUBLIC_ORIGIN ?? "";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new PublicOriginConfigurationError("APPSELL_PUBLIC_ORIGIN must be an absolute origin.");
  }
  if (
    raw !== url.origin ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new PublicOriginConfigurationError("APPSELL_PUBLIC_ORIGIN must contain only scheme and host.");
  }
  if (environment.NODE_ENV === "production" && url.protocol !== "https:") {
    throw new PublicOriginConfigurationError("Production public origin must use HTTPS.");
  }
  return url.origin;
}

export function hasConfiguredOrigin(request: Request, expectedOrigin: string) {
  const origin = request.headers.get("origin");
  return origin !== null && origin !== "null" && origin === expectedOrigin;
}
