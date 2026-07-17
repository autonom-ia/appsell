import { once } from "node:events";
import { createServer, type IncomingMessage, type Server } from "node:http";

import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { afterAll, beforeAll, describe, expect, test, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  IdentityOAuthAmbiguousError,
  IdentityOAuthClient,
  IdentityOAuthConfigurationError,
  IdentityOAuthProtocolError,
  parseIdentityOAuthConfig
} from "../lib/identity-oauth-client.server";

const LOGICAL_CLIENT_ID = "appsell-web";
const EXPECTED_AUDIENCE = LOGICAL_CLIENT_ID;
const NONCE = "n".repeat(43);
const STATE = "s".repeat(43);
const CHALLENGE = "c".repeat(43);
const VERIFIER = "v".repeat(43);
const SUBJECT = "identity-demo-user";
const EMAIL = "user.demo@autonomia.site";
const KEY_ID = "identity-test-key";
const PROVIDER_LONG_REFRESH = `${"A".repeat(600)}._-`;
const refreshScenarios = new Map<string, string>();

let server: Server;
let origin: string;
let signingKey: CryptoKey;
let otherSigningKey: CryptoKey;
let jwk: Awaited<ReturnType<typeof exportJWK>>;
const jwksRequests = new Map<string, number>();

beforeAll(async () => {
  const keyPair = await generateKeyPair("RS256");
  const otherKeyPair = await generateKeyPair("RS256");
  signingKey = keyPair.privateKey;
  otherSigningKey = otherKeyPair.privateKey;
  jwk = { ...await exportJWK(keyPair.publicKey), alg: "RS256", kid: KEY_ID, use: "sig" };
  server = createServer(async (request, response) => {
    try {
      if (request.url === "/jwks" || request.url === "/jwks-no-store" || request.url === "/jwks-no-cache" || request.url === "/jwks-long-cache") {
        jwksRequests.set(request.url, (jwksRequests.get(request.url) ?? 0) + 1);
        const cacheControl = request.url === "/jwks-no-store"
          ? "no-store"
          : request.url === "/jwks-no-cache"
            ? 'no-cache="set-cookie", max-age=600'
            : "public, max-age=600";
        response.writeHead(200, { "content-type": "application/json", "cache-control": cacheControl });
        response.end(JSON.stringify({ keys: [jwk] }));
        return;
      }
      if (request.url === "/redirect-jwks") {
        response.writeHead(302, { location: `${origin}/jwks` }).end();
        return;
      }
      if (request.url === "/wrong-content-jwks") {
        response.writeHead(200, { "content-type": "text/html" }).end("{}");
        return;
      }
      if (request.url === "/oversized-jwks") {
        response.writeHead(200, { "content-type": "application/json" }).end("x".repeat(2_000));
        return;
      }
      if (request.url !== "/token" || request.method !== "POST") {
        response.writeHead(404).end();
        return;
      }
      const form = new URLSearchParams(await requestBody(request));
      const grantType = form.get("grant_type");
      if (
        form.get("client_id") !== LOGICAL_CLIENT_ID ||
        (grantType === "authorization_code" && form.get("redirect_uri") !== "http://localhost:3000/auth/callback") ||
        (grantType !== "authorization_code" && grantType !== "refresh_token")
      ) {
        response.writeHead(400, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "invalid_request" }));
        return;
      }
      const refreshHandle = form.get("refresh_token") ?? "";
      const rawScenario = form.get("code") ?? "";
      const scenario = grantType === "refresh_token"
        ? refreshScenarios.get(refreshHandle) ?? "unknown-refresh"
        : rawScenario.replace(/-x{32}$/, "");
      if (scenario === "invalid-grant") {
        response.writeHead(401, { "content-type": "application/json" }).end(JSON.stringify({ error: "invalid_grant" }));
        return;
      }
      if (scenario === "in-progress") {
        response.writeHead(409, { "content-type": "application/json" }).end(JSON.stringify({ error: "refresh_in_progress" }));
        return;
      }
      if (scenario === "provider-502") {
        response.writeHead(502, { "content-type": "application/json" }).end(JSON.stringify({ error: "provider_unavailable" }));
        return;
      }
      if (scenario === "redirect") {
        response.writeHead(302, { location: `${origin}/token` }).end();
        return;
      }
      if (scenario === "wrong-content-type") {
        response.writeHead(200, { "content-type": "text/html" }).end("{}");
        return;
      }
      if (scenario === "oversized") {
        response.writeHead(200, { "content-type": "application/json" }).end("x".repeat(2_000));
        return;
      }
      if (scenario === "slow" || scenario === "slow-refresh") await new Promise((resolve) => setTimeout(resolve, 250));
      const tokens = await tokenResponse(scenario, grantType !== "refresh_token");
      if (grantType === "refresh_token") {
        if (scenario === "provider-omit-refresh") {
          delete tokens.refresh_token;
        } else if (scenario === "provider-long-refresh") {
          tokens.refresh_token = PROVIDER_LONG_REFRESH;
        } else {
          tokens.refresh_token = scenario === "same-refresh" ? refreshHandle : rotatedRefreshHandle(scenario);
        }
      }
      response.writeHead(200, { "content-type": "application/json; charset=UTF-8" });
      response.end(JSON.stringify(tokens));
    } catch {
      response.writeHead(500, { "content-type": "application/json" }).end("{}");
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("fake Identity did not bind");
  origin = `http://127.0.0.1:${address.port}`;
});

afterAll(async () => {
  server.close();
  await once(server, "close");
});

describe("Identity OAuth server adapter", () => {
  test("binds the logical OAuth client to the exact Foundation token audience", () => {
    const config = parseIdentityOAuthConfig(environment());
    expect(config.logicalOAuthClientId).toBe(LOGICAL_CLIENT_ID);
    expect(config.expectedAudience).toBe(EXPECTED_AUDIENCE);
    expect(config.redirectUri).toBe("http://localhost:3000/auth/callback");
    expect(config.tokenEndpoint.toString()).toBe(`${origin}/token`);
    expect(config.refreshHandleTtlSeconds).toBe(86_400);
  });

  test.each([
    ["malformed logical client", { APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: "Attacker Web" }],
    ["missing logical client", { APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: undefined }],
    ["missing token audience", { APPSELL_IDENTITY_EXPECTED_AUDIENCE: undefined }],
    ["malformed token audience", { APPSELL_IDENTITY_EXPECTED_AUDIENCE: "other client" }],
    ["unknown refresh mode", { APPSELL_IDENTITY_REFRESH_MODE: "rotate" }],
    ["unknown nonce mode", { APPSELL_IDENTITY_NONCE_MODE: "optional" }],
    ["foreign token origin", { APPSELL_IDENTITY_TOKEN_URL: "https://attacker.example/token" }],
    ["callback path", { APPSELL_IDENTITY_REDIRECT_URI: "http://localhost:3000/other" }],
    ["duplicate allowlist", { APPSELL_IDENTITY_ALLOWED_ORIGINS: `${origin},${origin}` }],
    ["unbounded timeout", { APPSELL_IDENTITY_HTTP_TIMEOUT_MS: "999999" }]
  ])("rejects fail-open configuration: %s", (_label, override) => {
    expect(() => parseIdentityOAuthConfig(environment(override))).toThrow(IdentityOAuthConfigurationError);
  });

  test("requires HTTPS outside an explicitly local runtime", () => {
    expect(() => parseIdentityOAuthConfig(environment({ NODE_ENV: "production" })))
      .toThrow("must use HTTPS");
  });

  test.each([undefined, "preview", "staging"])("rejects an implicit or unknown NODE_ENV: %s", (nodeEnvironment) => {
    expect(() => parseIdentityOAuthConfig(environment({ NODE_ENV: nodeEnvironment })))
      .toThrow(IdentityOAuthConfigurationError);
  });

  test("builds Authorization Code + PKCE with nonce and fixed client", () => {
    const client = new IdentityOAuthClient(parseIdentityOAuthConfig(environment()));
    const url = client.authorizationUrl({ state: STATE, nonce: NONCE, codeChallenge: CHALLENGE });
    expect(url.origin + url.pathname).toBe(`${origin}/authorize`);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: LOGICAL_CLIENT_ID,
      redirect_uri: "http://localhost:3000/auth/callback",
      response_type: "code",
      scope: "openid email profile",
      state: STATE,
      nonce: NONCE,
      code_challenge: CHALLENGE,
      code_challenge_method: "S256"
    });
  });

  test("rejects a nonce above the Foundation boundary before authorization or token exchange", async () => {
    const client = new IdentityOAuthClient(parseIdentityOAuthConfig(environment()));
    const nonce = "n".repeat(257);
    expect(() => client.authorizationUrl({ state: STATE, nonce, codeChallenge: CHALLENGE })).toThrow("nonce");
    await expect(client.exchangeAuthorizationCode({
      code: `valid-${"x".repeat(32)}`,
      codeVerifier: VERIFIER,
      expectedNonce: nonce
    })).rejects.toThrow("nonce");
  });

  test("exchanges and verifies a complete bundle for server-side vault delivery", async () => {
    const bundle = await exchange("valid");
    expect(bundle).toMatchObject({
      tokenType: "Bearer",
      identity: {
        issuer: `${origin}/issuer`,
        sub: SUBJECT,
        email: EMAIL,
        emailVerified: true
      }
    });
    expect(bundle.expiresIn).toBeGreaterThanOrEqual(298);
    expect(bundle.expiresIn).toBeLessThanOrEqual(300);
    expect(bundle.expiresAt).toBeGreaterThan(Date.now());
    expect(bundle.accessToken.split(".")).toHaveLength(3);
    expect(bundle.refreshToken).toBe("refresh-valid");
  });

  test("accepts the Foundation #29 response with no refresh token and preserves its absence", async () => {
    const bundle = await exchange("foundation-no-refresh");
    expect(bundle).toMatchObject({
      tokenType: "Bearer",
      identity: { issuer: `${origin}/issuer`, sub: SUBJECT, email: EMAIL, emailVerified: true }
    });
    expect(bundle).not.toHaveProperty("refreshToken");
  });

  test.each(["empty-refresh", "null-refresh", "oversized-refresh"])(
    "rejects a malformed refresh token when the optional field is present: %s",
    async (scenario) => {
      await expect(exchange(scenario)).rejects.toThrow(IdentityOAuthProtocolError);
    }
  );

  test("requires the exact Foundation audience on both signed tokens", async () => {
    await expect(exchange("missing-access-audience")).rejects.toThrow(IdentityOAuthProtocolError);
  });

  test("fails closed against the current upstream shape when the ID token omits nonce", async () => {
    await expect(exchange("missing-nonce")).rejects.toThrow("nonce");
  });

  test.each([
    "wrong-issuer",
    "wrong-id-audience",
    "multiple-id-audiences",
    "wrong-access-audience",
    "wrong-nonce",
    "wrong-token-use",
    "subject-mismatch",
    "email-unverified",
    "email-verified-string",
    "expired",
    "missing-exp",
    "wrong-signature",
    "missing-kid",
    "invalid-kid"
  ])("rejects invalid signed claims: %s", async (scenario) => {
    await expect(exchange(scenario)).rejects.toThrow(IdentityOAuthProtocolError);
  });

  test.each(["redirect", "wrong-content-type", "oversized"])("rejects unsafe HTTP response: %s", async (scenario) => {
    const override = scenario === "oversized" ? { APPSELL_IDENTITY_MAX_RESPONSE_BYTES: "1024" } : {};
    await expect(exchange(scenario, override)).rejects.toThrow(IdentityOAuthProtocolError);
  });

  test.each([
    ["redirect-jwks", {}],
    ["wrong-content-jwks", {}],
    ["oversized-jwks", { APPSELL_IDENTITY_MAX_RESPONSE_BYTES: "1024" }]
  ])("rejects unsafe JWKS response: %s", async (path, extra) => {
    await expect(exchange("valid", {
      APPSELL_IDENTITY_JWKS_URL: `${origin}/${path}`,
      ...extra
    })).rejects.toThrow(IdentityOAuthProtocolError);
  });

  test("bounds token endpoint latency", async () => {
    await expect(exchange("slow", { APPSELL_IDENTITY_HTTP_TIMEOUT_MS: "100" }))
      .rejects.toThrow(IdentityOAuthAmbiguousError);
  });

  test("classifies a Foundation 502 after code submission as ambiguous", async () => {
    await expect(exchange("provider-502")).rejects.toThrow(IdentityOAuthAmbiguousError);
  });

  test("respects no-store and caps a long JWKS max-age at five minutes", async () => {
    jwksRequests.set("/jwks-no-store", 0);
    const noStoreClient = new IdentityOAuthClient(parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_JWKS_URL: `${origin}/jwks-no-store`
    })));
    await exchangeWithClient(noStoreClient, "valid");
    await exchangeWithClient(noStoreClient, "valid");
    expect(jwksRequests.get("/jwks-no-store")).toBe(2);

    jwksRequests.set("/jwks-no-cache", 0);
    const noCacheClient = new IdentityOAuthClient(parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_JWKS_URL: `${origin}/jwks-no-cache`
    })));
    await exchangeWithClient(noCacheClient, "valid");
    await exchangeWithClient(noCacheClient, "valid");
    expect(jwksRequests.get("/jwks-no-cache")).toBe(2);

    jwksRequests.set("/jwks-long-cache", 0);
    let cacheNow = 1_000;
    const cappedClient = new IdentityOAuthClient(parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_JWKS_URL: `${origin}/jwks-long-cache`
    })), fetch, () => cacheNow);
    await exchangeWithClient(cappedClient, "valid");
    cacheNow += 5 * 60_000 - 1;
    await exchangeWithClient(cappedClient, "valid");
    expect(jwksRequests.get("/jwks-long-cache")).toBe(1);
    cacheNow += 2;
    await exchangeWithClient(cappedClient, "valid");
    expect(jwksRequests.get("/jwks-long-cache")).toBe(2);
  });

  test("rejects malformed PKCE before network access", async () => {
    const client = new IdentityOAuthClient(parseIdentityOAuthConfig(environment()));
    await expect(client.exchangeAuthorizationCode({
      code: "valid-valid-valid-valid-valid-valid",
      codeVerifier: "short",
      expectedNonce: NONCE
    })).rejects.toThrow("PKCE verifier");
  });

  test("refreshes and verifies a rotated logical handle without nonce", async () => {
    const result = await refresh("valid-refresh");
    expect(result).toMatchObject({
      kind: "success",
      scopes: ["openid", "profile"]
    });
    if (result.kind !== "success") throw new Error("Expected refresh success.");
    expect(result.refreshToken).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(result.accessTokenExpiresAt).toBeGreaterThan(Date.now());
    expect(result.refreshTokenExpiresAt).toBeGreaterThan(result.accessTokenExpiresAt);
  });

  test.each([
    ["invalid-grant", "invalid_grant"],
    ["in-progress", "transient"],
    ["provider-502", "ambiguous"],
    ["same-refresh", "ambiguous"],
    ["subject-mismatch", "ambiguous"],
    ["wrong-access-audience", "ambiguous"],
    ["refresh-with-nonce", "ambiguous"]
  ] as const)("maps refresh scenario %s to %s", async (scenario, kind) => {
    await expect(refresh(scenario)).resolves.toMatchObject({ kind });
  });

  test("treats a timed-out refresh as ambiguous and never retries internally", async () => {
    await expect(refresh("slow-refresh", { APPSELL_IDENTITY_HTTP_TIMEOUT_MS: "100" }))
      .resolves.toEqual({ kind: "ambiguous" });
  });

  // --- Compatibility modes (Option B) ---------------------------------------

  test("defaults the compatibility modes and preserves the appsell-web contract", () => {
    const config = parseIdentityOAuthConfig(environment());
    expect(config.logicalOAuthClientId).toBe(LOGICAL_CLIENT_ID);
    expect(config.expectedAudience).toBe(EXPECTED_AUDIENCE);
    expect(config.refreshMode).toBe("logical-handle");
    expect(config.nonceMode).toBe("required");
  });

  test("accepts a configurable logical client id and audience within the conservative pattern", () => {
    const config = parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: "appsell-cognito.web",
      APPSELL_IDENTITY_EXPECTED_AUDIENCE: "2h5b0c9d1e2f3g4h5i6j7k8l9m"
    }));
    expect(config.logicalOAuthClientId).toBe("appsell-cognito.web");
    expect(config.expectedAudience).toBe("2h5b0c9d1e2f3g4h5i6j7k8l9m");
  });

  test.each([
    ["uppercase", "Appsell-Web"],
    ["leading dot", ".appsell"],
    ["space", "appsell web"],
    ["slash", "appsell/web"],
    ["too long", "a".repeat(65)],
    ["empty", ""]
  ])("rejects a malformed configurable client id or audience: %s", (_label, value) => {
    expect(() => parseIdentityOAuthConfig(environment({ APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: value })))
      .toThrow(IdentityOAuthConfigurationError);
    expect(() => parseIdentityOAuthConfig(environment({ APPSELL_IDENTITY_EXPECTED_AUDIENCE: value })))
      .toThrow(IdentityOAuthConfigurationError);
  });

  test.each(["logical-handle", "provider-refresh"])("accepts a known refresh mode: %s", (mode) => {
    expect(parseIdentityOAuthConfig(environment({ APPSELL_IDENTITY_REFRESH_MODE: mode })).refreshMode).toBe(mode);
  });

  test.each(["required", "disabled"])("accepts a known nonce mode: %s", (mode) => {
    expect(parseIdentityOAuthConfig(environment({ APPSELL_IDENTITY_NONCE_MODE: mode })).nonceMode).toBe(mode);
  });

  test.each([
    ["APPSELL_IDENTITY_REFRESH_MODE", "rotate"],
    ["APPSELL_IDENTITY_NONCE_MODE", "optional"]
  ])("rejects an unknown mode value: %s=%s", (key, value) => {
    expect(() => parseIdentityOAuthConfig(environment({ [key]: value })))
      .toThrow(IdentityOAuthConfigurationError);
  });

  test("provider-refresh accepts a rotated opaque handle without the logical 43-char shape", async () => {
    const result = await refresh("provider-long-refresh", { APPSELL_IDENTITY_REFRESH_MODE: "provider-refresh" });
    expect(result.kind).toBe("success");
    if (result.kind !== "success") throw new Error("Expected refresh success.");
    expect(result.refreshToken).toBe(PROVIDER_LONG_REFRESH);
    expect(result.accessTokenExpiresAt).toBeGreaterThan(Date.now());
    expect(result.refreshTokenExpiresAt).toBeGreaterThan(result.accessTokenExpiresAt);
  });

  test("provider-refresh reuses the input handle when the provider omits rotation", async () => {
    const result = await refresh("provider-omit-refresh", { APPSELL_IDENTITY_REFRESH_MODE: "provider-refresh" });
    expect(result).toMatchObject({ kind: "success", refreshToken: refreshHandle("provider-omit-refresh") });
  });

  test("provider-refresh accepts an unrotated (same) handle instead of failing closed", async () => {
    await expect(refresh("same-refresh", { APPSELL_IDENTITY_REFRESH_MODE: "provider-refresh" }))
      .resolves.toMatchObject({ kind: "success" });
  });

  test.each([
    ["invalid-grant", "invalid_grant"],
    ["in-progress", "transient"],
    ["provider-502", "ambiguous"],
    ["subject-mismatch", "ambiguous"],
    ["wrong-access-audience", "ambiguous"],
    ["refresh-with-nonce", "ambiguous"]
  ] as const)("provider-refresh still hard-verifies the result: %s -> %s", async (scenario, kind) => {
    await expect(refresh(scenario, { APPSELL_IDENTITY_REFRESH_MODE: "provider-refresh" }))
      .resolves.toMatchObject({ kind });
  });

  test("logical-handle (default) rejects an omitted rotation as ambiguous", async () => {
    await expect(refresh("provider-omit-refresh")).resolves.toEqual({ kind: "ambiguous" });
  });

  test("nonce-disabled omits the nonce from the authorization request", () => {
    const client = new IdentityOAuthClient(parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_NONCE_MODE: "disabled"
    })));
    const url = client.authorizationUrl({ state: STATE, codeChallenge: CHALLENGE });
    expect(url.searchParams.has("nonce")).toBe(false);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: LOGICAL_CLIENT_ID,
      redirect_uri: "http://localhost:3000/auth/callback",
      response_type: "code",
      scope: "openid email profile",
      state: STATE,
      code_challenge: CHALLENGE,
      code_challenge_method: "S256"
    });
  });

  test("nonce-disabled exchanges an ID token that omits the nonce", async () => {
    const bundle = await exchange("missing-nonce", { APPSELL_IDENTITY_NONCE_MODE: "disabled" });
    expect(bundle).toMatchObject({
      tokenType: "Bearer",
      identity: { sub: SUBJECT, email: EMAIL, emailVerified: true }
    });
  });

  test("nonce-disabled rejects an ID token that carries an unexpected nonce", async () => {
    await expect(exchange("valid", { APPSELL_IDENTITY_NONCE_MODE: "disabled" }))
      .rejects.toThrow("nonce");
  });
});

async function exchange(scenario: string, override: Record<string, string | undefined> = {}) {
  const client = new IdentityOAuthClient(parseIdentityOAuthConfig(environment(override)));
  return exchangeWithClient(client, scenario);
}

async function exchangeWithClient(client: IdentityOAuthClient, scenario: string) {
  return client.exchangeAuthorizationCode({
    code: `${scenario}-${"x".repeat(32)}`,
    codeVerifier: VERIFIER,
    expectedNonce: NONCE
  });
}

function environment(override: Record<string, string | undefined> = {}) {
  return {
    NODE_ENV: "test",
    APPSELL_PUBLIC_ORIGIN: "http://localhost:3000",
    APPSELL_IDENTITY_ISSUER: `${origin}/issuer`,
    APPSELL_IDENTITY_AUTHORIZATION_URL: `${origin}/authorize`,
    APPSELL_IDENTITY_TOKEN_URL: `${origin}/token`,
    APPSELL_IDENTITY_JWKS_URL: `${origin}/jwks`,
    APPSELL_IDENTITY_ALLOWED_ORIGINS: origin,
    APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: LOGICAL_CLIENT_ID,
    APPSELL_IDENTITY_EXPECTED_AUDIENCE: EXPECTED_AUDIENCE,
    APPSELL_IDENTITY_REDIRECT_URI: "http://localhost:3000/auth/callback",
    APPSELL_IDENTITY_REFRESH_HANDLE_TTL_SECONDS: "86400",
    ...override
  };
}

async function tokenResponse(scenario: string, includeNonce = true) {
  const now = Math.floor(Date.now() / 1_000);
  const issuer = scenario === "wrong-issuer" ? "https://attacker.example" : `${origin}/issuer`;
  const idAudience = scenario === "wrong-id-audience"
    ? "other-client"
    : scenario === "multiple-id-audiences"
      ? [EXPECTED_AUDIENCE, "other-client"]
      : EXPECTED_AUDIENCE;
  const accessSubject = scenario === "subject-mismatch" ? "different-subject" : SUBJECT;
  const protectedHeader = scenario === "missing-kid"
    ? { alg: "RS256" as const, typ: "JWT" }
    : { alg: "RS256" as const, kid: scenario === "invalid-kid" ? "bad kid" : KEY_ID, typ: "JWT" };
  const access = new SignJWT({
    token_use: scenario === "wrong-token-use" ? "id" : "access"
  })
    .setProtectedHeader(protectedHeader)
    .setIssuer(issuer)
    .setSubject(accessSubject)
    .setIssuedAt(now);
  if (scenario !== "missing-access-audience") {
    access.setAudience(scenario === "wrong-access-audience" ? "other-client" : EXPECTED_AUDIENCE);
  }
  const idClaims: Record<string, unknown> = {
    token_use: "id",
    email: EMAIL,
    email_verified: scenario === "email-verified-string"
      ? "true"
      : scenario !== "email-unverified"
  };
  if (includeNonce && scenario !== "missing-nonce") {
    idClaims.nonce = scenario === "wrong-nonce" ? "different-nonce-value-that-is-long-enough" : NONCE;
  }
  if (!includeNonce && scenario === "refresh-with-nonce") idClaims.nonce = NONCE;
  const identity = new SignJWT(idClaims)
    .setProtectedHeader(protectedHeader)
    .setIssuer(issuer)
    .setAudience(idAudience)
    .setSubject(SUBJECT)
    .setIssuedAt(now);
  if (scenario !== "missing-exp") {
    access.setExpirationTime(scenario === "expired" ? now - 10 : now + 300);
    identity.setExpirationTime(scenario === "expired" ? now - 10 : now + 300);
  }
  const key = scenario === "wrong-signature" ? otherSigningKey : signingKey;
  const response: Record<string, unknown> = {
    access_token: await access.sign(key),
    id_token: await identity.sign(key),
    token_type: "Bearer",
    expires_in: 300
  };
  if (scenario === "foundation-no-refresh") return response;
  response.refresh_token = scenario === "empty-refresh"
    ? ""
    : scenario === "null-refresh"
      ? null
      : scenario === "oversized-refresh"
        ? "r".repeat(16 * 1_024 + 1)
        : `refresh-${scenario}`;
  return response;
}

async function refresh(scenario: string, override: Record<string, string | undefined> = {}) {
  const handle = refreshHandle(scenario);
  refreshScenarios.set(handle, scenario);
  const client = new IdentityOAuthClient(parseIdentityOAuthConfig(environment(override)));
  return client.refresh({
    subject: SUBJECT,
    refreshToken: handle,
    scopes: ["openid", "profile"],
    revision: 1,
    idempotencyKey: "operation-1",
    signal: new AbortController().signal
  });
}

function refreshHandle(scenario: string) {
  return Buffer.from(`refresh:${scenario}`, "utf8").toString("base64url").padEnd(43, "x").slice(0, 43);
}

function rotatedRefreshHandle(scenario: string) {
  return Buffer.from(`rotated:${scenario}`, "utf8").toString("base64url").padEnd(43, "y").slice(0, 43);
}

async function requestBody(request: IncomingMessage) {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks).toString("utf8");
}
