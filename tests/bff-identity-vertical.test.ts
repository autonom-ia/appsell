import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { createServer, type IncomingMessage } from "node:http";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { expect, test } from "vitest";

import { sha256 } from "../lib/crypto";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

test("real Next routes complete the isolated Identity PKCE vertical without exposing credentials", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const bffPort = await unusedNonDefaultPort();
  const identityPort = await unusedNonDefaultPort();
  const identityOrigin = `http://127.0.0.1:${identityPort}`;
  const issuer = `${identityOrigin}/identity`;
  const suffix = `${process.pid}-${Date.now()}`;
  const distDir = `.next-identity-${suffix}`;
  const keyPair = await generateKeyPair("RS256");
  const publicJwk = {
    ...await exportJWK(keyPair.publicKey),
    alg: "RS256",
    kid: "appsell-identity-test-key",
    use: "sig"
  };
  const grants = new Map<string, { nonce: string; challenge: string }>();
  const tokenRequests: Array<{ verifier: string; challenge: string }> = [];
  const identity = createIdentityServer({
    issuer,
    redirectUri: `http://localhost:${bffPort}/auth/callback`,
    publicJwk,
    signingKey: keyPair.privateKey,
    grants,
    tokenRequests
  });
  await new Promise<void>((resolve, reject) => {
    identity.once("error", reject);
    identity.listen(identityPort, "127.0.0.1", resolve);
  });
  const vaultKey = randomBytes(32);
  const bff = startBff(bffPort, distDir, {
    NODE_ENV: "development",
    NEXT_TELEMETRY_DISABLED: "1",
    APPSELL_OAUTH_MODE: "identity",
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:identity-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_PUBLIC_ORIGIN: `http://localhost:${bffPort}`,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "test-v1", key: vaultKey.toString("base64"), active: true }
    ]),
    APPSELL_DOMAIN_API_URL: "",
    APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: "appsell-web",
    APPSELL_IDENTITY_EXPECTED_AUDIENCE: "appsell-web",
    APPSELL_IDENTITY_ISSUER: issuer,
    APPSELL_IDENTITY_AUTHORIZATION_URL: `${identityOrigin}/authorize`,
    APPSELL_IDENTITY_TOKEN_URL: `${identityOrigin}/oauth/token`,
    APPSELL_IDENTITY_JWKS_URL: `${identityOrigin}/jwks`,
    APPSELL_IDENTITY_ALLOWED_ORIGINS: identityOrigin,
    APPSELL_IDENTITY_REDIRECT_URI: `http://localhost:${bffPort}/auth/callback`,
    APPSELL_IDENTITY_REFRESH_HANDLE_TTL_SECONDS: "86400"
  });

  try {
    await waitForBff(bff, bffPort);
    const login = await fetch(`http://127.0.0.1:${bffPort}/auth/login`, { redirect: "manual" });
    expect(login.status).toBe(307);
    const transactionCookie = cookiePair(login.headers.get("set-cookie"), "appsell_oauth_tx");
    const authorization = await fetch(requiredHeader(login, "location"), { redirect: "manual" });
    expect(authorization.status).toBe(307);
    const callbackUrl = requiredHeader(authorization, "location");

    const callback = await fetch(callbackUrl, {
      redirect: "manual",
      headers: { cookie: transactionCookie }
    });
    expect(callback.status).toBe(307);
    expect(requiredHeader(callback, "location")).toBe(`http://localhost:${bffPort}/dashboard`);
    const sessionCookie = cookiePair(callback.headers.get("set-cookie"), "appsell_session");
    expect(tokenRequests).toHaveLength(1);
    expect(sha256(tokenRequests[0]!.verifier)).toBe(tokenRequests[0]!.challenge);

    const session = await fetch(`http://127.0.0.1:${bffPort}/api/session`, {
      headers: { cookie: sessionCookie }
    });
    expect(session.status).toBe(200);
    const sessionBody = await session.json() as Record<string, unknown>;
    expect(sessionBody).toMatchObject({ authenticated: true, demoProfile: null });
    expect(JSON.stringify(sessionBody)).not.toContain("buyer.identity@autonomia.site");
    expect(JSON.stringify(sessionBody)).not.toContain("identity-access-token");

    const dashboard = await fetch(`http://127.0.0.1:${bffPort}/dashboard`, {
      headers: { cookie: sessionCookie }
    });
    expect(dashboard.status).toBe(200);
    expect(await dashboard.text()).toContain("Seu negócio em um só lugar.");

    const retry = await fetch(callbackUrl, {
      redirect: "manual",
      headers: { cookie: transactionCookie }
    });
    expect(retry.status).toBe(307);
    expect(cookiePair(retry.headers.get("set-cookie"), "appsell_session")).toBe(sessionCookie);
    expect(tokenRequests).toHaveLength(1);

    const keys = await redis.client.keys(`appsell:test:identity-${suffix}:*`);
    const values = await Promise.all(keys.map((key) => redis.client.get(key)));
    const persisted = values.filter((value): value is string => value !== null).join("\n");
    expect(persisted).not.toContain("identity-access-token");
    expect(persisted).not.toContain("buyer.identity@autonomia.site");
    expect(persisted).not.toContain("r".repeat(43));
  } finally {
    await stopBff(bff);
    identity.close();
    await once(identity, "close").catch(() => undefined);
    await redis.stop();
    await rm(join(process.cwd(), distDir), { recursive: true, force: true });
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 40_000);

function createIdentityServer(input: {
  issuer: string;
  redirectUri: string;
  publicJwk: Record<string, unknown>;
  signingKey: CryptoKey;
  grants: Map<string, { nonce: string; challenge: string }>;
  tokenRequests: Array<{ verifier: string; challenge: string }>;
}) {
  return createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? "/", input.issuer);
      if (request.method === "GET" && url.pathname === "/jwks") {
        response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
        response.end(JSON.stringify({ keys: [input.publicJwk] }));
        return;
      }
      if (request.method === "GET" && url.pathname === "/authorize") {
        if (
          url.searchParams.get("client_id") !== "appsell-web" ||
          url.searchParams.get("redirect_uri") !== input.redirectUri ||
          url.searchParams.get("response_type") !== "code" ||
          url.searchParams.get("code_challenge_method") !== "S256"
        ) {
          response.writeHead(400).end();
          return;
        }
        const state = url.searchParams.get("state") ?? "";
        const nonce = url.searchParams.get("nonce") ?? "";
        const challenge = url.searchParams.get("code_challenge") ?? "";
        const code = randomBytes(32).toString("base64url");
        input.grants.set(code, { nonce, challenge });
        const callback = new URL(input.redirectUri);
        callback.searchParams.set("code", code);
        callback.searchParams.set("state", state);
        response.writeHead(307, { location: callback.toString(), "cache-control": "no-store" }).end();
        return;
      }
      if (request.method === "POST" && url.pathname === "/oauth/token") {
        const form = new URLSearchParams(await requestBody(request));
        const code = form.get("code") ?? "";
        const verifier = form.get("code_verifier") ?? "";
        const grant = input.grants.get(code);
        if (
          form.get("grant_type") !== "authorization_code" ||
          form.get("client_id") !== "appsell-web" ||
          form.get("redirect_uri") !== input.redirectUri ||
          !grant ||
          sha256(verifier) !== grant.challenge
        ) {
          response.writeHead(400, { "content-type": "application/json" }).end(JSON.stringify({ error: "invalid_grant" }));
          return;
        }
        input.grants.delete(code);
        input.tokenRequests.push({ verifier, challenge: grant.challenge });
        const [accessToken, idToken] = await Promise.all([
          signedToken(input, { token_use: "access" }),
          signedToken(input, {
            token_use: "id",
            nonce: grant.nonce,
            email: "buyer.identity@autonomia.site",
            email_verified: true
          })
        ]);
        response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
        response.end(JSON.stringify({
          access_token: accessToken,
          id_token: idToken,
          refresh_token: "r".repeat(43),
          token_type: "Bearer",
          expires_in: 300
        }));
        return;
      }
      response.writeHead(404).end();
    } catch {
      response.writeHead(500, { "content-type": "application/json" }).end("{}");
    }
  });
}

function signedToken(
  input: { issuer: string; signingKey: CryptoKey },
  claims: Record<string, unknown>
) {
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid: "appsell-identity-test-key" })
    .setIssuer(input.issuer)
    .setAudience("appsell-web")
    .setSubject("identity-buyer-001")
    .setIssuedAt()
    .setExpirationTime("5m")
    .sign(input.signingKey);
}

function requestBody(request: IncomingMessage) {
  return new Promise<string>((resolve, reject) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(process.execPath, [join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", String(port)], {
    cwd: process.cwd(),
    env: { ...process.env, ...shared, APPSELL_NEXT_DIST_DIR: distDir },
    stdio: ["ignore", "pipe", "pipe"]
  });
}

async function waitForBff(process: ChildProcess, port: number) {
  const deadline = Date.now() + 20_000;
  let diagnostics = "";
  process.stderr?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-4_000); });
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`BFF ${port} exited early: ${diagnostics}`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/`);
      if (response.ok) return;
    } catch {
      // Next is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`BFF ${port} did not become ready: ${diagnostics}`);
}

function requiredHeader(response: Response, name: string) {
  const value = response.headers.get(name);
  if (!value) throw new Error(`Missing ${name} response header.`);
  return value;
}

function cookiePair(setCookie: string | null, name: string) {
  const match = setCookie?.match(new RegExp(`(?:^|,\\s*)(${name}=[^;]+)`));
  if (!match) throw new Error(`Missing ${name} cookie.`);
  return match[1] ?? "";
}

async function stopBff(process: ChildProcess) {
  if (process.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => process.once("exit", () => resolve()));
  process.kill("SIGTERM");
  await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 3_000))]);
  if (process.exitCode === null) {
    process.kill("SIGKILL");
    await exited;
  }
}
