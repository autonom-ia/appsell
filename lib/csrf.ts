import { createHmac, timingSafeEqual } from "node:crypto";

const ACTION_PATTERN = /^(POST|PUT|PATCH|DELETE):\/[a-z0-9/_-]{1,120}$/;

export class CsrfConfigurationError extends Error {}

export function parseCsrfKey(encoded: string, forbiddenKeys: readonly Buffer[]) {
  if (!Array.isArray(forbiddenKeys)) {
    throw new CsrfConfigurationError("CSRF key separation list is required.");
  }
  const key = Buffer.from(encoded, "base64");
  if (key.length !== 32 || key.toString("base64") !== encoded) {
    throw new CsrfConfigurationError("APPSELL_CSRF_HMAC_KEY_B64 must be canonical base64 for 32 bytes.");
  }
  if (forbiddenKeys.some((forbidden) => forbidden.length === key.length && timingSafeEqual(forbidden, key))) {
    throw new CsrfConfigurationError("CSRF signing key must be independent.");
  }
  return key;
}

export function deriveCsrfToken(sessionId: string, action: string, key: Buffer) {
  if (!sessionId || sessionId.length > 256 || !ACTION_PATTERN.test(action) || key.length !== 32) {
    throw new CsrfConfigurationError("Invalid CSRF token context.");
  }
  return createHmac("sha256", key)
    .update(`appsell.csrf.v1\n${sessionId}\n${action}`, "utf8")
    .digest("base64url");
}

export function verifyCsrfToken(token: string | null | undefined, sessionId: string, action: string, key: Buffer) {
  if (!token) return false;
  const expected = deriveCsrfToken(sessionId, action, key);
  const actual = Buffer.from(token);
  const expectedBuffer = Buffer.from(expected);
  return actual.length === expectedBuffer.length && timingSafeEqual(actual, expectedBuffer);
}
