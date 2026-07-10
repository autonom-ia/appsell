export interface OAuthTokenRefreshInput {
  subject: string;
  refreshToken: string;
  scopes: string[];
  revision: number;
  /** Stable for one token revision. Providers must use it for deduplication when supported. */
  idempotencyKey: string;
  signal: AbortSignal;
}

export interface OAuthTokenRefreshSuccess {
  kind: "success";
  accessToken: string;
  refreshToken: string;
  accessTokenExpiresAt: number;
  refreshTokenExpiresAt: number;
  scopes: string[];
}

export type OAuthTokenRefreshResult =
  | OAuthTokenRefreshSuccess
  | { kind: "invalid_grant" }
  /** The provider proves no externally visible rotation or side effect occurred. */
  | { kind: "transient" }
  /** A rotation or other provider side effect may have occurred; automatic retry is forbidden. */
  | { kind: "ambiguous" };

export interface OAuthTokenRefresher {
  /**
   * Must not retry an uncertain upstream call internally. Throws, timeouts, malformed
   * responses, lost responses, and any uncertainty are semantically `ambiguous`.
   * `transient` is allowed only when the adapter can prove that no side effect occurred.
   */
  refresh(input: OAuthTokenRefreshInput): Promise<OAuthTokenRefreshResult>;
}

export class OAuthRefreshProviderProtocolError extends Error {}

const MAX_TOKEN_BYTES = 16 * 1024;
const MAX_SCOPE_BYTES = 256;
const MAX_SCOPES = 64;

export function parseRefreshProviderResult(value: unknown): OAuthTokenRefreshResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new OAuthRefreshProviderProtocolError("Token refresh provider returned an invalid result.");
  }
  const record = value as Record<string, unknown>;
  if (record.kind === "invalid_grant" || record.kind === "transient" || record.kind === "ambiguous") {
    if (Object.keys(record).length !== 1) {
      throw new OAuthRefreshProviderProtocolError("Token refresh provider returned an invalid result.");
    }
    return { kind: record.kind };
  }
  if (
    record.kind !== "success" ||
    Object.keys(record).sort().join(",") !==
      "accessToken,accessTokenExpiresAt,kind,refreshToken,refreshTokenExpiresAt,scopes" ||
    !validBoundedString(record.accessToken, MAX_TOKEN_BYTES) ||
    !validBoundedString(record.refreshToken, MAX_TOKEN_BYTES) ||
    !positiveSafeInteger(record.accessTokenExpiresAt) ||
    !positiveSafeInteger(record.refreshTokenExpiresAt) ||
    record.refreshTokenExpiresAt <= record.accessTokenExpiresAt ||
    !Array.isArray(record.scopes) ||
    record.scopes.length > MAX_SCOPES ||
    !record.scopes.every((scope) => validBoundedString(scope, MAX_SCOPE_BYTES))
  ) {
    throw new OAuthRefreshProviderProtocolError("Token refresh provider returned an invalid result.");
  }
  return {
    kind: "success",
    accessToken: record.accessToken,
    refreshToken: record.refreshToken,
    accessTokenExpiresAt: record.accessTokenExpiresAt,
    refreshTokenExpiresAt: record.refreshTokenExpiresAt,
    scopes: [...record.scopes]
  };
}

function positiveSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function validBoundedString(value: unknown, maxBytes: number): value is string {
  return typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxBytes &&
    Buffer.byteLength(value, "utf8") <= maxBytes;
}
