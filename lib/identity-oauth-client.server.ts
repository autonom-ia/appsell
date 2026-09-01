import "server-only";

import {
  createRemoteJWKSet,
  customFetch,
  jwtVerify,
  type JWSHeaderParameters,
  type JWTPayload
} from "jose";
import type {
  OAuthTokenRefreshInput,
  OAuthTokenRefreshResult,
  OAuthTokenRefresher
} from "./oauth-refresh";

const DEFAULT_TIMEOUT_MS = 5_000;
const DEFAULT_MAX_RESPONSE_BYTES = 32 * 1_024;
const MAX_RESPONSE_BYTES = 64 * 1_024;
const MAX_JWKS_CACHE_MS = 5 * 60_000;
const TOKEN_MAX_BYTES = 16 * 1_024;
const OPAQUE_VALUE = /^[A-Za-z0-9._~-]{32,512}$/;
const OIDC_NONCE = /^[A-Za-z0-9._~-]{32,256}$/;
const PKCE_VERIFIER = /^[A-Za-z0-9._~-]{43,128}$/;
const PKCE_CHALLENGE = /^[A-Za-z0-9_-]{43}$/;
const KEY_ID = /^[A-Za-z0-9._~+/=-]{1,256}$/;
// Conservative shape for the configurable logical OAuth client id / expected
// audience: lowercase, digits and a short safe punctuation set. Accepts the
// legacy `appsell-web` default and Cognito app-client ids while rejecting
// spaces, uppercase, empty values and unbounded input (fail-closed).
const OAUTH_IDENTIFIER = /^[a-z0-9][a-z0-9._-]{0,63}$/;
// Opaque provider refresh token (e.g. Cognito) accepted in provider-refresh
// mode. Broader charset/length than the 43-char logical rotation handle, still
// bounded to keep the value safe to log-shape and store.
const PROVIDER_REFRESH_TOKEN = /^[A-Za-z0-9._~+/=-]{20,4096}$/;

export type IdentityRefreshMode = "logical-handle" | "provider-refresh";
// "broker": the upstream is not an OpenID Provider but a broker that relays a
// third-party ID token (e.g. Cognito) it did not mint for this client. It
// neither honours the nonce this adapter sends nor omits one of its own, so the
// value carries no meaning here and is accepted without comparison.
//
// The single-use code, PKCE S256 and state keep a third party from replaying or
// swapping the code, and keep the ID token off the front-channel. They do NOT
// prove the returned bundle belongs to this authorization: that binding
// (code -> logical client + redirect URI + PKCE -> stored tokens) lives in the
// broker. This mode therefore has a precondition — the broker must keep that
// binding — and a regression there is not detectable here. See
// docs/identity-server-adapter.md. `iss`, `aud`, the RS256 signature,
// `email_verified` and the access/identity subject match stay fail-closed.
// Revert to "required" once autonom-ia/autonomia-identity-foundation#29 mints
// logical ID tokens.
export type IdentityNonceMode = "required" | "disabled" | "broker";

// Explicit contract for how the ID token nonce must be treated, so the meaning
// never has to be inferred from an absent argument at each call site.
export type NonceExpectation =
  | { readonly kind: "match"; readonly value: string }
  | { readonly kind: "absent" }
  | { readonly kind: "unverified" };

type Environment = Partial<Record<string, string | undefined>>;

export interface IdentityOAuthConfig {
  readonly issuer: string;
  readonly authorizationEndpoint: URL;
  readonly registrationEndpoint: URL;
  readonly tokenEndpoint: URL;
  readonly jwksEndpoint: URL;
  readonly logicalOAuthClientId: string;
  readonly expectedAudience: string;
  readonly redirectUri: string;
  readonly timeoutMs: number;
  readonly maxResponseBytes: number;
  readonly refreshHandleTtlSeconds: number;
  readonly refreshMode: IdentityRefreshMode;
  readonly nonceMode: IdentityNonceMode;
}

export interface VerifiedIdentityTokenBundle {
  readonly accessToken: string;
  readonly idToken: string;
  readonly refreshToken?: string;
  readonly tokenType: "Bearer";
  readonly expiresIn: number;
  readonly expiresAt: number;
  readonly refreshTokenExpiresAt?: number;
  readonly identity: {
    readonly issuer: string;
    readonly sub: string;
    readonly email: string;
    readonly emailVerified: true;
  };
}

export class IdentityOAuthConfigurationError extends Error {
  override readonly name = "IdentityOAuthConfigurationError";
}

export class IdentityOAuthProtocolError extends Error {
  override readonly name = "IdentityOAuthProtocolError";
}

export class IdentityOAuthUnavailableError extends Error {
  override readonly name = "IdentityOAuthUnavailableError";
}

export class IdentityOAuthAmbiguousError extends Error {
  override readonly name = "IdentityOAuthAmbiguousError";
}

export function parseIdentityOAuthConfig(environment: Environment = process.env): IdentityOAuthConfig {
  const nodeEnvironment = required(environment.NODE_ENV, "NODE_ENV");
  if (!new Set(["development", "test", "production"]).has(nodeEnvironment)) {
    throw new IdentityOAuthConfigurationError("NODE_ENV must explicitly be development, test or production.");
  }
  const publicOrigin = strictOrigin(required(environment.APPSELL_PUBLIC_ORIGIN, "APPSELL_PUBLIC_ORIGIN"));
  const issuer = strictIssuer(required(environment.APPSELL_IDENTITY_ISSUER, "APPSELL_IDENTITY_ISSUER"));
  const authorizationEndpoint = strictEndpoint(
    required(environment.APPSELL_IDENTITY_AUTHORIZATION_URL, "APPSELL_IDENTITY_AUTHORIZATION_URL"),
    "APPSELL_IDENTITY_AUTHORIZATION_URL"
  );
  // A tela de cadastro do Identity vive ao lado da de autorizacao. Deriva-se dela
  // para nao exigir uma variavel nova em todo ambiente, e a variavel explicita
  // continua disponivel para quando os dois caminhos divergirem.
  const registrationEndpoint = strictEndpoint(
    environment.APPSELL_IDENTITY_REGISTRATION_URL ?? siblingPath(authorizationEndpoint, "register"),
    "APPSELL_IDENTITY_REGISTRATION_URL"
  );
  const tokenEndpoint = strictEndpoint(
    required(environment.APPSELL_IDENTITY_TOKEN_URL, "APPSELL_IDENTITY_TOKEN_URL"),
    "APPSELL_IDENTITY_TOKEN_URL"
  );
  const jwksEndpoint = strictEndpoint(
    required(environment.APPSELL_IDENTITY_JWKS_URL, "APPSELL_IDENTITY_JWKS_URL"),
    "APPSELL_IDENTITY_JWKS_URL"
  );
  const logicalOAuthClientId = trustedOAuthIdentifier(
    required(environment.APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID, "APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID"),
    "APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID"
  );
  const expectedAudience = trustedOAuthIdentifier(
    required(environment.APPSELL_IDENTITY_EXPECTED_AUDIENCE, "APPSELL_IDENTITY_EXPECTED_AUDIENCE"),
    "APPSELL_IDENTITY_EXPECTED_AUDIENCE"
  );
  const refreshMode = parseRefreshMode(environment.APPSELL_IDENTITY_REFRESH_MODE);
  const nonceMode = parseNonceMode(environment.APPSELL_IDENTITY_NONCE_MODE);

  const allowedOrigins = parseAllowedOrigins(
    required(environment.APPSELL_IDENTITY_ALLOWED_ORIGINS, "APPSELL_IDENTITY_ALLOWED_ORIGINS")
  );
  for (const [label, url] of [
    ["issuer", new URL(issuer)],
    ["authorization endpoint", authorizationEndpoint],
    ["registration endpoint", registrationEndpoint],
    ["token endpoint", tokenEndpoint],
    ["JWKS endpoint", jwksEndpoint]
  ] as const) {
    if (!allowedOrigins.has(url.origin)) {
      throw new IdentityOAuthConfigurationError(`Identity ${label} origin is not allowlisted.`);
    }
    requireSecureTransport(url, nodeEnvironment, label);
  }
  requireSecureTransport(new URL(publicOrigin), nodeEnvironment, "public origin");

  const redirectUri = required(environment.APPSELL_IDENTITY_REDIRECT_URI, "APPSELL_IDENTITY_REDIRECT_URI");
  if (redirectUri !== `${publicOrigin}/auth/callback`) {
    throw new IdentityOAuthConfigurationError("APPSELL_IDENTITY_REDIRECT_URI must be the exact AppSell callback.");
  }

  return {
    issuer,
    authorizationEndpoint,
    registrationEndpoint,
    tokenEndpoint,
    jwksEndpoint,
    logicalOAuthClientId,
    expectedAudience,
    redirectUri,
    timeoutMs: boundedInteger(environment.APPSELL_IDENTITY_HTTP_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, 100, 10_000),
    maxResponseBytes: boundedInteger(
      environment.APPSELL_IDENTITY_MAX_RESPONSE_BYTES,
      DEFAULT_MAX_RESPONSE_BYTES,
      1_024,
      MAX_RESPONSE_BYTES
    ),
    refreshHandleTtlSeconds: boundedInteger(
      required(environment.APPSELL_IDENTITY_REFRESH_HANDLE_TTL_SECONDS, "APPSELL_IDENTITY_REFRESH_HANDLE_TTL_SECONDS"),
      24 * 60 * 60,
      2 * 60 * 60,
      30 * 24 * 60 * 60
    ),
    refreshMode,
    nonceMode
  };
}

export class IdentityOAuthClient implements OAuthTokenRefresher {
  private readonly jwks: ReturnType<typeof createRemoteJWKSet>;

  constructor(
    private readonly config: IdentityOAuthConfig,
    private readonly fetcher: typeof fetch = fetch,
    private readonly cacheClock: () => number = Date.now
  ) {
    let jwksCache: { readonly body: string; readonly expiresAt: number } | undefined;
    const jwksFetcher: typeof fetch = async (input, init) => {
      if (jwksCache && this.cacheClock() < jwksCache.expiresAt) {
        return jsonResponse(jwksCache.body);
      }
      let response: Response;
      try {
        const signals = [init?.signal, AbortSignal.timeout(config.timeoutMs)]
          .filter((signal): signal is AbortSignal => signal instanceof AbortSignal);
        response = await fetcher(input, {
          ...init,
          cache: "no-store",
          credentials: "omit",
          redirect: "manual",
          signal: signals.length === 1 ? signals[0] : AbortSignal.any(signals)
        });
      } catch {
        throw new IdentityOAuthUnavailableError("Identity JWKS endpoint is unavailable.");
      }
      if (response.status !== 200 || !isJsonContentType(response.headers.get("content-type"))) {
        throw new IdentityOAuthProtocolError("Identity JWKS response is invalid.");
      }
      const body = await readBoundedBody(response, config.maxResponseBytes);
      const cacheTtlMs = boundedJwksCacheTtl(response.headers);
      jwksCache = cacheTtlMs > 0
        ? { body, expiresAt: this.cacheClock() + cacheTtlMs }
        : undefined;
      return jsonResponse(body);
    };
    this.jwks = createRemoteJWKSet(config.jwksEndpoint, {
      timeoutDuration: config.timeoutMs,
      cooldownDuration: 30_000,
      // The custom fetcher owns Cache-Control-aware caching. Keeping jose's
      // cache disabled prevents its default ten-minute TTL from outliving the
      // upstream directive or the five-minute local ceiling.
      cacheMaxAge: 0,
      [customFetch]: jwksFetcher
    });
  }

  authorizationUrl(input: { state: string; nonce?: string; codeChallenge: string }) {
    requireOpaque(input.state, "state");
    if (!PKCE_CHALLENGE.test(input.codeChallenge)) {
      throw new IdentityOAuthProtocolError("OAuth PKCE challenge is invalid.");
    }
    const parameters: Record<string, string> = {
      client_id: this.config.logicalOAuthClientId,
      redirect_uri: this.config.redirectUri,
      response_type: "code",
      scope: "openid email profile",
      state: input.state,
      code_challenge: input.codeChallenge,
      code_challenge_method: "S256"
    };
    // Only "required" sends a nonce. "disabled" omits it and rejects any ID
    // token that echoes one back; "broker" omits it because the upstream
    // discards it, and accepts whatever the relayed token happens to carry.
    if (this.config.nonceMode === "required") {
      requireNonce(input.nonce);
      parameters.nonce = input.nonce as string;
    }
    const url = new URL(this.config.authorizationEndpoint);
    url.search = new URLSearchParams(parameters).toString();
    return url;
  }

  // Mesma carga do authorize, apontada para a tela de cadastro. O Identity le
  // client_id, redirect_uri, state e code_challenge da propria query string,
  // entao o pedido sobrevive a criacao da conta.
  registrationUrl(input: { state: string; nonce?: string; codeChallenge: string }) {
    const url = new URL(this.config.registrationEndpoint);
    url.search = this.authorizationUrl(input).search;
    return url;
  }

  async exchangeAuthorizationCode(input: {
    code: string;
    codeVerifier: string;
    expectedNonce?: string;
  }): Promise<VerifiedIdentityTokenBundle> {
    requireOpaque(input.code, "authorization code");
    const nonceExpectation = this.nonceExpectationFor(input.expectedNonce);
    if (!PKCE_VERIFIER.test(input.codeVerifier)) {
      throw new IdentityOAuthProtocolError("OAuth PKCE verifier is invalid.");
    }

    let response: Response;
    try {
      response = await this.fetcher(this.config.tokenEndpoint, {
        method: "POST",
        headers: {
          accept: "application/json",
          "content-type": "application/x-www-form-urlencoded"
        },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: this.config.logicalOAuthClientId,
          code: input.code,
          redirect_uri: this.config.redirectUri,
          code_verifier: input.codeVerifier
        }),
        cache: "no-store",
        credentials: "omit",
        redirect: "manual",
        signal: AbortSignal.timeout(this.config.timeoutMs)
      });
    } catch {
      throw new IdentityOAuthAmbiguousError("Identity token exchange outcome is ambiguous.");
    }
    if (response.type === "opaqueredirect" || response.status === 429 || response.status >= 500) {
      throw new IdentityOAuthAmbiguousError("Identity token exchange outcome is ambiguous.");
    }
    if (response.status !== 200) {
      throw new IdentityOAuthProtocolError("Identity token exchange was rejected.");
    }
    if (!isJsonContentType(response.headers.get("content-type"))) {
      throw new IdentityOAuthProtocolError("Identity token response content type is invalid.");
    }
    let raw: string;
    try {
      raw = await readBoundedBody(response, this.config.maxResponseBytes);
    } catch (error) {
      if (error instanceof IdentityOAuthProtocolError) throw error;
      throw new IdentityOAuthAmbiguousError("Identity token exchange outcome is ambiguous.");
    }
    const payload = parseTokenResponse(raw);

    return this.verifyTokenResponse(payload, nonceExpectation);
  }

  // Authorization-code exchange is the only path that can carry a nonce the
  // adapter itself generated. Refresh never can, so it verifies with "absent"
  // unless the upstream is a broker whose relayed token is not ours to police.
  private nonceExpectationFor(candidate: string | undefined): NonceExpectation {
    if (this.config.nonceMode === "broker") return { kind: "unverified" };
    if (this.config.nonceMode === "disabled") return { kind: "absent" };
    return { kind: "match", value: requireNonce(candidate) };
  }

  async refresh(input: OAuthTokenRefreshInput): Promise<OAuthTokenRefreshResult> {
    let subject: string;
    try {
      subject = canonicalSubject(input.subject);
      requireRefreshTokenInput(input.refreshToken, this.config.refreshMode);
      if (!Array.isArray(input.scopes) || input.scopes.length === 0) return { kind: "ambiguous" };
    } catch {
      return { kind: "ambiguous" };
    }

    let response: Response;
    try {
      response = await this.fetcher(this.config.tokenEndpoint, {
        method: "POST",
        headers: {
          accept: "application/json",
          "content-type": "application/x-www-form-urlencoded"
        },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          client_id: this.config.logicalOAuthClientId,
          refresh_token: input.refreshToken
        }),
        cache: "no-store",
        credentials: "omit",
        redirect: "manual",
        signal: AbortSignal.any([input.signal, AbortSignal.timeout(this.config.timeoutMs)])
      });
    } catch {
      return { kind: "ambiguous" };
    }
    if (response.status === 401) return { kind: "invalid_grant" };
    if (response.status === 409) return { kind: "transient" };
    if (response.status !== 200 || response.type === "opaqueredirect") return { kind: "ambiguous" };
    if (!isJsonContentType(response.headers.get("content-type"))) return { kind: "ambiguous" };

    try {
      const raw = await readBoundedBody(response, this.config.maxResponseBytes);
      const payload = parseTokenResponse(raw);
      const rotatedRefreshToken = resolveRefreshRotation(
        payload.refreshToken,
        input.refreshToken,
        this.config.refreshMode
      );
      if (rotatedRefreshToken === undefined) return { kind: "ambiguous" };
      const verified = await this.verifyTokenResponse(
        payload,
        this.config.nonceMode === "broker"
          ? { kind: "unverified" }
          : { kind: "absent" }
      );
      if (verified.identity.sub !== subject) return { kind: "ambiguous" };
      return {
        kind: "success",
        accessToken: verified.accessToken,
        refreshToken: rotatedRefreshToken,
        accessTokenExpiresAt: verified.expiresAt,
        refreshTokenExpiresAt: Date.now() + this.config.refreshHandleTtlSeconds * 1_000,
        scopes: [...input.scopes]
      };
    } catch {
      return { kind: "ambiguous" };
    }
  }

  private async verifyTokenResponse(
    payload: ParsedTokenResponse,
    nonce: NonceExpectation
  ): Promise<VerifiedIdentityTokenBundle> {
    let access: JWTPayload;
    let identity: JWTPayload;
    try {
      const [accessResult, identityResult] = await Promise.all([
        // Cognito access tokens carry the client in `client_id`, not `aud` (id tokens
        // keep `aud`). Audience is enforced for both shapes in requireAccessTokenClaims.
        jwtVerify(payload.accessToken, this.jwks, {
          issuer: this.config.issuer,
          algorithms: ["RS256"]
        }),
        jwtVerify(payload.idToken, this.jwks, {
          issuer: this.config.issuer,
          audience: this.config.expectedAudience,
          algorithms: ["RS256"]
        })
      ]);
      requireProtectedHeader(accessResult.protectedHeader);
      requireProtectedHeader(identityResult.protectedHeader);
      access = accessResult.payload;
      identity = identityResult.payload;
    } catch (error) {
      // Name the failed claim so a misconfigured issuer/audience is
      // distinguishable from a bad signature. Only the claim name is surfaced,
      // never a token value or the expected configuration.
      throw new IdentityOAuthProtocolError(
        `Identity token verification failed (${describeVerificationFailure(error)}).`
      );
    }
    requireAccessTokenClaims(access, this.config);
    requireIdTokenClaims(identity, this.config);
    if (nonce.kind === "match" && identity.nonce !== nonce.value) {
      throw new IdentityOAuthProtocolError("Identity nonce is invalid.");
    }
    if (nonce.kind === "absent" && identity.nonce !== undefined) {
      // Reached by the refresh leg and by the code leg in "disabled" mode, so
      // the message must not claim to be refresh-specific.
      throw new IdentityOAuthProtocolError("Identity token contains an unexpected nonce.");
    }
    if (access.sub !== identity.sub) {
      throw new IdentityOAuthProtocolError("Identity token subjects do not match.");
    }
    const sub = canonicalSubject(identity.sub);
    const email = canonicalEmail(identity.email);
    if (identity.email_verified !== true) {
      // Três situações muito diferentes chegavam aqui com a mesma frase, e a
      // diferença entre elas é a diferença entre "o aluno precisa confirmar o
      // e-mail" e "o provedor não está mandando o claim".
      //
      // A mensagem continua sem valor nenhum — o `reason` é lido por telemetria
      // e não pode virar oráculo de configuração. Nomear a **forma** do claim
      // não revela o que ele contém, e é o que separa um problema do aluno de
      // um problema de integração.
      throw new IdentityOAuthProtocolError(
        identity.email_verified === undefined
          ? "Identity email verification claim is absent."
          : typeof identity.email_verified !== "boolean"
            ? "Identity email verification claim is not a boolean."
            : "Identity email is not verified."
      );
    }
    const now = Date.now();
    const expiresAt = Math.min(
      (access.exp as number) * 1_000,
      (identity.exp as number) * 1_000,
      now + payload.expiresIn * 1_000
    );
    const expiresIn = Math.floor((expiresAt - now) / 1_000);
    if (expiresIn < 1) throw new IdentityOAuthProtocolError("Identity token expiry is too short.");

    return {
      accessToken: payload.accessToken,
      idToken: payload.idToken,
      ...(payload.refreshToken === undefined ? {} : { refreshToken: payload.refreshToken }),
      tokenType: "Bearer",
      expiresIn,
      expiresAt,
      ...(payload.refreshToken === undefined ? {} : {
        refreshTokenExpiresAt: now + this.config.refreshHandleTtlSeconds * 1_000
      }),
      identity: {
        issuer: this.config.issuer,
        sub,
        email,
        emailVerified: true
      }
    };
  }
}

interface ParsedTokenResponse {
  accessToken: string;
  idToken: string;
  refreshToken?: string;
  expiresIn: number;
}

function parseTokenResponse(raw: string): ParsedTokenResponse {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new IdentityOAuthProtocolError("Identity token response is invalid.");
  }
  if (!isRecord(value) || value.token_type !== "Bearer") {
    throw new IdentityOAuthProtocolError("Identity token response is incomplete.");
  }
  const accessToken = boundedToken(value.access_token, "access token");
  const idToken = boundedToken(value.id_token, "ID token");
  const refreshToken = value.refresh_token === undefined
    ? undefined
    : boundedToken(value.refresh_token, "refresh token");
  if (!Number.isSafeInteger(value.expires_in) || (value.expires_in as number) < 1 || (value.expires_in as number) > 86_400) {
    throw new IdentityOAuthProtocolError("Identity token expiry is invalid.");
  }
  return {
    accessToken,
    idToken,
    ...(refreshToken === undefined ? {} : { refreshToken }),
    expiresIn: value.expires_in as number
  };
}

async function readBoundedBody(response: Response, limit: number) {
  if (!response.body) throw new IdentityOAuthProtocolError("Identity token response body is missing.");
  const declaredLength = response.headers.get("content-length");
  if (declaredLength && (!/^\d+$/.test(declaredLength) || Number(declaredLength) > limit)) {
    await response.body.cancel().catch(() => undefined);
    throw new IdentityOAuthProtocolError("Identity token response is too large.");
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) {
      await reader.cancel().catch(() => undefined);
      throw new IdentityOAuthProtocolError("Identity token response is too large.");
    }
    chunks.push(value);
  }
  const joined = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(joined);
  } catch {
    throw new IdentityOAuthProtocolError("Identity token response encoding is invalid.");
  }
}

function requireAccessTokenClaims(payload: JWTPayload, config: IdentityOAuthConfig) {
  // Cognito-shaped access tokens omit `aud` and carry the app client in `client_id`;
  // generic OIDC providers keep `aud`. Exactly one of the two must match the audience.
  const audienceMatches = payload.aud === undefined
    ? payload.client_id === config.expectedAudience
    : payload.aud === config.expectedAudience;
  if (
    payload.token_use !== "access" ||
    payload.iss !== config.issuer ||
    !audienceMatches ||
    !Number.isSafeInteger(payload.exp) ||
    (payload.exp as number) * 1_000 <= Date.now()
  ) {
    throw new IdentityOAuthProtocolError("Identity token claims are invalid.");
  }
  canonicalSubject(payload.sub);
}

function requireIdTokenClaims(payload: JWTPayload, config: IdentityOAuthConfig) {
  if (
    payload.token_use !== "id" ||
    payload.iss !== config.issuer ||
    payload.aud !== config.expectedAudience ||
    !Number.isSafeInteger(payload.exp) ||
    (payload.exp as number) * 1_000 <= Date.now()
  ) {
    throw new IdentityOAuthProtocolError("Identity token claims are invalid.");
  }
  canonicalSubject(payload.sub);
}

function requireProtectedHeader(header: JWSHeaderParameters) {
  if (header.alg !== "RS256" || typeof header.kid !== "string" || !KEY_ID.test(header.kid)) {
    throw new IdentityOAuthProtocolError("Identity token key identifier is invalid.");
  }
}

function boundedToken(value: unknown, label: string) {
  if (typeof value !== "string" || value.length < 1 || Buffer.byteLength(value, "utf8") > TOKEN_MAX_BYTES) {
    throw new IdentityOAuthProtocolError(`Identity ${label} is invalid.`);
  }
  return value;
}

function requireLogicalRefreshHandle(value: string) {
  if (!/^[A-Za-z0-9_-]{43}$/.test(value)) {
    throw new IdentityOAuthProtocolError("Identity logical refresh handle is invalid.");
  }
}

function canonicalSubject(value: unknown) {
  if (typeof value !== "string" || value.length < 3 || value.length > 255 || value !== value.trim() || /[\p{Cc}\p{Cs}]/u.test(value)) {
    throw new IdentityOAuthProtocolError("Identity subject is invalid.");
  }
  return value;
}

function canonicalEmail(value: unknown) {
  if (typeof value !== "string" || value !== value.trim().toLowerCase() || value.length > 320 || !/^[^\s@]+@[^\s@]+$/.test(value)) {
    throw new IdentityOAuthProtocolError("Identity email is invalid.");
  }
  return value;
}

function parseAllowedOrigins(raw: string) {
  const values = raw.split(",");
  if (values.length === 0 || values.some((value) => !value || value !== value.trim())) {
    throw new IdentityOAuthConfigurationError("APPSELL_IDENTITY_ALLOWED_ORIGINS is invalid.");
  }
  const origins = new Set(values.map(strictOrigin));
  if (origins.size !== values.length) {
    throw new IdentityOAuthConfigurationError("APPSELL_IDENTITY_ALLOWED_ORIGINS contains duplicates.");
  }
  return origins;
}

function strictOrigin(raw: string) {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new IdentityOAuthConfigurationError("Identity origin is invalid.");
  }
  if (raw !== url.origin || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new IdentityOAuthConfigurationError("Identity origin must contain only scheme and host.");
  }
  return url.origin;
}

function strictIssuer(raw: string) {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new IdentityOAuthConfigurationError("APPSELL_IDENTITY_ISSUER must be an absolute URL.");
  }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new IdentityOAuthConfigurationError("APPSELL_IDENTITY_ISSUER is invalid.");
  }
  if (url.pathname.endsWith("/") && url.pathname !== "/") {
    throw new IdentityOAuthConfigurationError("APPSELL_IDENTITY_ISSUER must not end with a slash.");
  }
  return url.toString().replace(/\/$/, "");
}

// Troca o ultimo segmento do caminho, preservando origem e prefixo: um issuer em
// https://id.exemplo/oauth/authorize vira https://id.exemplo/oauth/register.
function siblingPath(base: URL, segment: string) {
  const url = new URL(base);
  // Producao serve o endpoint com barra final ("/login/"). Sem descartar a
  // barra, o ultimo segmento seria vazio e o caminho viraria "/login/register".
  const parts = url.pathname.replace(/\/+$/, "").split("/");
  parts[parts.length - 1] = segment;
  url.pathname = parts.join("/");
  return url.toString();
}

function strictEndpoint(raw: string, label: string) {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new IdentityOAuthConfigurationError(`${label} must be an absolute URL.`);
  }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname === "/") {
    throw new IdentityOAuthConfigurationError(`${label} is invalid.`);
  }
  return url;
}

function requireSecureTransport(url: URL, nodeEnvironment: string, label: string) {
  if (url.protocol === "https:") return;
  if (
    (nodeEnvironment === "development" || nodeEnvironment === "test") &&
    url.protocol === "http:" &&
    isLoopback(url.hostname)
  ) return;
  throw new IdentityOAuthConfigurationError(`Identity ${label} must use HTTPS.`);
}

function isLoopback(hostname: string) {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

function boundedInteger(raw: string | undefined, fallback: number, minimum: number, maximum: number) {
  if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw)) throw new IdentityOAuthConfigurationError("Identity numeric configuration is invalid.");
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new IdentityOAuthConfigurationError("Identity numeric configuration is out of range.");
  }
  return value;
}

function trustedOAuthIdentifier(value: string, label: string) {
  if (!OAUTH_IDENTIFIER.test(value)) {
    throw new IdentityOAuthConfigurationError(`${label} is invalid.`);
  }
  return value;
}

function parseRefreshMode(raw: string | undefined): IdentityRefreshMode {
  if (raw === undefined || raw === "logical-handle") return "logical-handle";
  if (raw === "provider-refresh") return "provider-refresh";
  throw new IdentityOAuthConfigurationError(
    "APPSELL_IDENTITY_REFRESH_MODE must be logical-handle or provider-refresh."
  );
}

// Maps a jose verification failure to a short, allowlisted reason. Never
// includes the token, the claim value or the configured expectation — only
// which check failed, so a misconfigured issuer/audience is diagnosable from
// logs without leaking configuration to the caller.
function describeVerificationFailure(error: unknown): string {
  const code = typeof error === "object" && error !== null && "code" in error
    ? String((error as { code: unknown }).code)
    : "";
  switch (code) {
    case "ERR_JWT_CLAIM_VALIDATION_FAILED": {
      const claim = typeof error === "object" && error !== null && "claim" in error
        ? String((error as { claim: unknown }).claim)
        : "unknown";
      return CLAIM_NAMES.has(claim) ? `claim ${claim}` : "claim mismatch";
    }
    case "ERR_JWT_EXPIRED":
      return "expired";
    case "ERR_JWS_SIGNATURE_VERIFICATION_FAILED":
      return "signature";
    case "ERR_JWKS_NO_MATCHING_KEY":
      return "no matching JWKS key";
    case "ERR_JOSE_ALG_NOT_ALLOWED":
      return "algorithm not allowed";
    default:
      return "malformed or unverifiable token";
  }
}

const CLAIM_NAMES = new Set(["iss", "aud", "sub", "exp", "nbf", "iat", "jti", "typ"]);

function parseNonceMode(raw: string | undefined): IdentityNonceMode {
  if (raw === undefined || raw === "required") return "required";
  if (raw === "disabled") return "disabled";
  if (raw === "broker") return "broker";
  throw new IdentityOAuthConfigurationError(
    "APPSELL_IDENTITY_NONCE_MODE must be required, disabled or broker."
  );
}

function requireProviderRefreshToken(value: string) {
  if (!PROVIDER_REFRESH_TOKEN.test(value)) {
    throw new IdentityOAuthProtocolError("Identity provider refresh token is invalid.");
  }
}

function requireRefreshTokenInput(value: string, mode: IdentityRefreshMode) {
  if (mode === "provider-refresh") {
    requireProviderRefreshToken(value);
    return;
  }
  requireLogicalRefreshHandle(value);
}

// Returns the refresh token to persist, or undefined to fail closed (ambiguous).
// logical-handle: rotation is mandatory — a missing or unchanged handle is
// ambiguous, and the new handle must match the strict 43-char shape.
// provider-refresh: rotation is optional — reuse the input token when the
// provider omits one, otherwise accept the rotated opaque token.
function resolveRefreshRotation(
  next: string | undefined,
  current: string,
  mode: IdentityRefreshMode
): string | undefined {
  if (mode === "provider-refresh") {
    if (next === undefined) return current;
    requireProviderRefreshToken(next);
    return next;
  }
  if (!next || next === current) return undefined;
  requireLogicalRefreshHandle(next);
  return next;
}

function requireOpaque(value: string, label: string) {
  if (!OPAQUE_VALUE.test(value)) throw new IdentityOAuthProtocolError(`OAuth ${label} is invalid.`);
}

function requireNonce(value: string | undefined): string {
  if (value === undefined || !OIDC_NONCE.test(value)) {
    throw new IdentityOAuthProtocolError("OAuth nonce is invalid.");
  }
  return value;
}

function boundedJwksCacheTtl(headers: Headers) {
  const cacheControl = headers.get("cache-control");
  if (!cacheControl || /(?:^|,)\s*(?:no-store|no-cache)(?:\s*=\s*"[^"]*")?\s*(?:,|$)/i.test(cacheControl)) return 0;
  const match = cacheControl.match(/(?:^|,)\s*max-age\s*=\s*"?(\d+)"?\s*(?:,|$)/i);
  if (!match) return 0;
  const maxAgeSeconds = Number(match[1]);
  const ageHeader = headers.get("age");
  const ageSeconds = ageHeader && /^\d+$/.test(ageHeader) ? Number(ageHeader) : 0;
  if (!Number.isSafeInteger(maxAgeSeconds) || !Number.isSafeInteger(ageSeconds)) return 0;
  return Math.max(0, Math.min(maxAgeSeconds - ageSeconds, MAX_JWKS_CACHE_MS / 1_000)) * 1_000;
}

function jsonResponse(body: string) {
  return new Response(body, {
    status: 200,
    headers: { "content-type": "application/json" }
  });
}

function required(value: string | undefined, label: string) {
  if (!value) throw new IdentityOAuthConfigurationError(`${label} is required.`);
  return value;
}

function isJsonContentType(value: string | null) {
  return value !== null && /^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
