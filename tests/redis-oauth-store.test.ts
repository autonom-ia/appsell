import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { hmacSha256, sha256 } from "../lib/crypto";
import {
  OAuthIdentityCallbackUnavailableError,
  OAuthStoreConfigurationError,
  OAuthStoreUnavailableError,
  OAuthVaultConfigurationError,
  OAuthVaultIntegrityError,
  RedisOAuthService
} from "../lib/redis-oauth-service";
import { EnvelopeTokenVault, LocalAesKeyEnvelopeProvider } from "../lib/token-vault";
import { startIsolatedRedis, type IsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

let isolated: IsolatedRedis;
let sequence = 0;
const sessionHmacKey = Buffer.alloc(32, 7);
const vaultKey = Buffer.alloc(32, 9);

beforeAll(async () => {
  isolated = await startIsolatedRedis();
}, 10_000);

afterAll(async () => {
  if (isolated) await isolated.stop();
});

function service(suffix = `case-${++sequence}`, overrides: Record<string, number> = {}) {
  return new RedisOAuthService({
    redisUrl: isolated.url,
    prefix: `appsell:test:${suffix}`,
    sessionHmacKey,
    tokenVault: testTokenVault(),
    ...overrides
  });
}

function testTokenVault() {
  return new EnvelopeTokenVault(new LocalAesKeyEnvelopeProvider([
    { id: "test-v1", key: vaultKey, active: true }
  ], { nodeEnvironment: "test", forbiddenKeys: [sessionHmacKey] }));
}

async function loginAndCode(store: RedisOAuthService) {
  const login = await store.beginLogin("integration-client");
  const code = await store.authorize({
    state: login.state,
    nonce: login.nonce,
    codeChallenge: login.codeChallenge
  }, "integration-client");
  return { login, code };
}

describe("Redis OAuth store", () => {
  it("exchanges a real PKCE transaction into an encrypted Identity session and preserves identity on refresh", async () => {
    const suffix = `identity-${++sequence}`;
    const store = service(suffix);
    const login = await store.beginLogin("identity-client");
    const now = Date.now();
    const accessToken = "identity-access-token";
    const refreshToken = "r".repeat(43);
    const calls: Array<{ code: string; codeVerifier: string; expectedNonce: string }> = [];
    const exchanger = {
      async exchangeAuthorizationCode(input: { code: string; codeVerifier: string; expectedNonce: string }) {
        calls.push(input);
        return {
          accessToken,
          refreshToken,
          tokenType: "Bearer" as const,
          expiresAt: now + 60_000,
          refreshTokenExpiresAt: now + 5 * 60_000,
          identity: {
            issuer: "http://127.0.0.1:4010/identity",
            sub: "identity-user-001",
            email: "buyer.demo@autonomia.site",
            emailVerified: true as const
          }
        };
      }
    };
    const code = "c".repeat(43);
    const sessionId = (await store.callbackIdentity({
      transactionId: login.transactionId,
      code,
      state: login.state
    }, exchanger)).sessionId;

    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ code, expectedNonce: login.nonce });
    expect(sha256(calls[0]!.codeVerifier)).toBe(login.codeChallenge);
    expect(await store.getSession(sessionId)).toMatchObject({
      subject: "identity-user-001",
      tokenRevision: 1,
      identity: {
        issuer: "http://127.0.0.1:4010/identity",
        email: "buyer.demo@autonomia.site",
        emailVerified: true
      }
    });
    expect((await store.callbackIdentity({
      transactionId: login.transactionId,
      code,
      state: login.state
    }, exchanger)).sessionId).toBe(sessionId);
    expect(calls).toHaveLength(1);
    // O callback de Identity também grava o par num instante só (#511).
    const pairPrefix = `appsell:test:${suffix}:{appsell:test:${suffix}}`;
    const [identitySessionAt, identityVaultAt] = await Promise.all([
      isolated.client.call("PEXPIRETIME", `${pairPrefix}:session:${sha256(sessionId)}`),
      isolated.client.call("PEXPIRETIME", `${pairPrefix}:vault:${sha256(sessionId)}`)
    ]);
    expect(identitySessionAt).toBeGreaterThan(Date.now());
    expect(identitySessionAt).toBe(identityVaultAt);

    const keys = await isolated.client.keys(`appsell:test:${suffix}:{appsell:test:${suffix}}:*`);
    const values = await Promise.all(keys.map((key) => isolated.client.get(key)));
    const persisted = values.filter((value): value is string => value !== null).join("\n");
    expect(persisted).not.toContain(accessToken);
    expect(persisted).not.toContain(refreshToken);
    expect(persisted).not.toContain("buyer.demo@autonomia.site");

    const refreshed = await store.refreshSession(sessionId, 1, {
      async refresh() {
        return {
          kind: "success" as const,
          accessToken: "identity-access-token-rotated",
          refreshToken: "s".repeat(43),
          accessTokenExpiresAt: Date.now() + 60_000,
          refreshTokenExpiresAt: Date.now() + 5 * 60_000,
          scopes: ["email", "openid", "profile"]
        };
      }
    });
    expect(refreshed.revision).toBe(2);
    expect(refreshed.identity).toEqual({
      issuer: "http://127.0.0.1:4010/identity",
      email: "buyer.demo@autonomia.site",
      emailVerified: true
    });
    await store.close();
  });

  it("rejects Identity state before calling the token endpoint", async () => {
    const store = service();
    const login = await store.beginLogin("identity-client");
    let called = false;
    await expect(store.callbackIdentity({
      transactionId: login.transactionId,
      code: "c".repeat(43),
      state: "x".repeat(43)
    }, {
      async exchangeAuthorizationCode() {
        called = true;
        throw new Error("must not be called");
      }
    })).rejects.toThrow("Invalid OAuth state");
    expect(called).toBe(false);
    await store.close();
  });

  it("serializes 20 cross-instance Identity callbacks into one external exchange", async () => {
    const suffix = `identity-concurrent-${++sequence}`;
    const first = service(suffix);
    const second = new RedisOAuthService({
      redisUrl: isolated.url,
      prefix: `appsell:test:${suffix}`,
      sessionHmacKey,
      tokenVault: testTokenVault()
    });
    const login = await first.beginLogin("identity-client");
    let exchangeCalls = 0;
    const exchanger = {
      async exchangeAuthorizationCode() {
        exchangeCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 50));
        const now = Date.now();
        return {
          accessToken: "identity-concurrent-access",
          refreshToken: "t".repeat(43),
          tokenType: "Bearer" as const,
          expiresAt: now + 60_000,
          refreshTokenExpiresAt: now + 5 * 60_000,
          identity: {
            issuer: "http://127.0.0.1:4010/identity",
            sub: "identity-concurrent-user",
            email: "concurrent@autonomia.site",
            emailVerified: true as const
          }
        };
      }
    };
    const callback = {
      transactionId: login.transactionId,
      code: "d".repeat(43),
      state: login.state
    };
    const attempts = await Promise.allSettled(Array.from({ length: 20 }, (_, index) =>
      (index % 2 ? first : second).callbackIdentity(callback, exchanger)
    ));
    expect(attempts.filter((attempt) => attempt.status === "fulfilled")).toHaveLength(20);
    const sessionIds = attempts.flatMap((attempt) => attempt.status === "fulfilled" ? [attempt.value.sessionId] : []);
    expect(new Set(sessionIds)).toHaveLength(1);
    expect(exchangeCalls).toBe(1);
    expect(await first.getSession(sessionIds[0])).toMatchObject({
      subject: "identity-concurrent-user",
      identity: { email: "concurrent@autonomia.site", emailVerified: true }
    });
    const sessionKeys = await isolated.client.keys(`appsell:test:${suffix}:{appsell:test:${suffix}}:session:*`);
    const vaultKeys = await isolated.client.keys(`appsell:test:${suffix}:{appsell:test:${suffix}}:vault:*`);
    expect(sessionKeys).toHaveLength(1);
    expect(vaultKeys).toHaveLength(1);
    await first.close();
    await second.close();
  });

  it("marks an ambiguous Identity exchange and blocks blind code retry", async () => {
    const store = service();
    const login = await store.beginLogin("identity-client");
    let calls = 0;
    const callback = {
      transactionId: login.transactionId,
      code: "e".repeat(43),
      state: login.state
    };
    await expect(store.callbackIdentity(callback, {
      async exchangeAuthorizationCode() {
        calls += 1;
        const error = new Error("ambiguous");
        error.name = "IdentityOAuthAmbiguousError";
        throw error;
      }
    })).rejects.toMatchObject({ name: "IdentityOAuthAmbiguousError" });
    await expect(store.callbackIdentity(callback, {
      async exchangeAuthorizationCode() {
        calls += 1;
        throw new Error("must not retry");
      }
    })).rejects.toBeInstanceOf(OAuthIdentityCallbackUnavailableError);
    expect(calls).toBe(1);
    await store.close();
  });

  it("creates one session and returns the same id for 20 concurrent callback retries", async () => {
    const suffix = `concurrent-${++sequence}`;
    const store = service(suffix);
    const peer = new RedisOAuthService({
      redisUrl: isolated.url,
      prefix: `appsell:test:${suffix}`,
      sessionHmacKey,
      tokenVault: testTokenVault()
    });
    const { login, code } = await loginAndCode(store);
    const attempts = await Promise.allSettled(Array.from({ length: 20 }, (_, index) => (index % 2 ? peer : store).callback({
      transactionId: login.transactionId,
      code,
      state: login.state
    })));
    expect(attempts.filter((attempt) => attempt.status === "fulfilled")).toHaveLength(20);
    const sessionIds = attempts.flatMap((attempt) => attempt.status === "fulfilled" ? [attempt.value] : []);
    expect(new Set(sessionIds)).toHaveLength(1);
    const sessionKeys = await isolated.client.keys(`appsell:test:${suffix}:{appsell:test:${suffix}}:session:*`);
    expect(sessionKeys).toHaveLength(1);
    const vaultKeys = await isolated.client.keys(`appsell:test:${suffix}:{appsell:test:${suffix}}:vault:*`);
    expect(vaultKeys).toHaveLength(1);
    await store.close();
    await peer.close();
  });

  it("shares transactions and sessions between independent service instances", async () => {
    const prefix = `appsell:test:shared-${++sequence}`;
    const first = new RedisOAuthService({
      redisUrl: isolated.url,
      prefix,
      sessionHmacKey,
      tokenVault: testTokenVault()
    });
    const second = new RedisOAuthService({
      redisUrl: isolated.url,
      prefix,
      sessionHmacKey,
      tokenVault: testTokenVault()
    });
    const login = await first.beginLogin();
    const code = await second.authorize({ state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge });
    const sessionId = await second.callback({ transactionId: login.transactionId, code, state: login.state });
    expect(await first.getSession(sessionId)).toMatchObject({ subject: "dev-user-001" });
    expect(await first.getServerTokenBundle(sessionId)).toEqual(await second.getServerTokenBundle(sessionId));
    await first.close();
    await second.close();
  });

  it("uses Redis TTLs for transactions, authorization codes and sessions", async () => {
    const txStore = service(undefined, { transactionTtlMs: 40, codeTtlMs: 1_000, sessionTtlMs: 40 });
    const expired = await loginAndCode(txStore);
    await new Promise((resolve) => setTimeout(resolve, 80));
    await expect(txStore.callback({
      transactionId: expired.login.transactionId,
      code: expired.code,
      state: expired.login.state
    })).rejects.toThrow("Login transaction expired");

    const active = await loginAndCode(txStore);
    const sessionId = await txStore.callback({
      transactionId: active.login.transactionId,
      code: active.code,
      state: active.login.state
    });
    const activePrefix = `appsell:test:case-${sequence}:{appsell:test:case-${sequence}}`;
    const sessionKey = `${activePrefix}:session:${sha256(sessionId)}`;
    const vaultKey = `${activePrefix}:vault:${sha256(sessionId)}`;
    expect(await isolated.client.pttl(sessionKey)).toBeGreaterThan(0);
    // O par vence no mesmo milissegundo (#511): antes havia 5 ms de folga
    // aqui, e era essa folga que escondia o descompasso no Redis 7.0.
    const [sessionAt, vaultAt] = await Promise.all([
      isolated.client.call("PEXPIRETIME", sessionKey),
      isolated.client.call("PEXPIRETIME", vaultKey)
    ]);
    expect(typeof sessionAt).toBe("number");
    expect(sessionAt).toBe(vaultAt);
    expect(await txStore.getSession(sessionId)).not.toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(await txStore.getSession(sessionId)).toBeNull();
    await txStore.close();
  });

  it("fails closed when Redis is unavailable and never falls back to memory", async () => {
    const port = await unusedNonDefaultPort();
    const store = new RedisOAuthService({
      redisUrl: `redis://127.0.0.1:${port}/0`,
      prefix: `appsell:test:offline-${++sequence}`,
      sessionHmacKey,
      tokenVault: testTokenVault(),
      connectTimeoutMs: 100
    });
    await expect(store.beginLogin()).rejects.toBeInstanceOf(OAuthStoreUnavailableError);
    await store.close();
  });

  it("isolates namespaces and never stores raw opaque identifiers", async () => {
    const suffix = `isolation-${++sequence}`;
    const first = service(`${suffix}-first`);
    const second = service(`${suffix}-second`);
    const { login, code } = await loginAndCode(first);
    const sessionId = await first.callback({ transactionId: login.transactionId, code, state: login.state });
    expect(await second.getSession(sessionId)).toBeNull();

    const bundle = await first.getServerTokenBundle(sessionId);
    expect(bundle).not.toBeNull();

    const keys = await isolated.client.keys(
      `appsell:test:${suffix}-first:{appsell:test:${suffix}-first}:*`
    );
    const values = await Promise.all(keys.map((key) => isolated.client.get(key)));
    const persisted = [...keys, ...values.filter((value): value is string => value !== null)].join("\n");
    for (const opaque of [login.transactionId, code, login.state, sessionId]) expect(persisted).not.toContain(opaque);
    expect(persisted).not.toContain(bundle!.accessToken);
    expect(persisted).not.toContain(bundle!.refreshToken);
    expect(keys.some((key) => key.endsWith(sha256(sessionId)))).toBe(true);
    await first.close();
    await second.close();
  });

  it("atomically rate-limits per client without allowing partial overrun", async () => {
    const store = service(undefined, { maxPerClient: 2, maxGlobal: 10, rateWindowMs: 1_000 });
    const attempts = await Promise.allSettled(Array.from({ length: 10 }, () => store.beginLogin("same-client")));
    expect(attempts.filter((attempt) => attempt.status === "fulfilled")).toHaveLength(2);
    expect(attempts.filter((attempt) => attempt.status === "rejected")).toHaveLength(8);
    await store.close();
  });

  it("revokes a session atomically", async () => {
    const store = service();
    const { login, code } = await loginAndCode(store);
    const sessionId = await store.callback({ transactionId: login.transactionId, code, state: login.state });
    await store.logout(sessionId);
    expect(await store.getSession(sessionId)).toBeNull();
    expect(await store.getServerTokenBundle(sessionId)).toBeNull();
    await store.close();
  });

  it("accepts a lost-response retry only with the same code and state fingerprint", async () => {
    const store = service();
    const { login, code } = await loginAndCode(store);
    const first = await store.callback({ transactionId: login.transactionId, code, state: login.state });
    expect(await store.callback({ transactionId: login.transactionId, code, state: login.state })).toBe(first);
    await expect(store.callback({
      transactionId: login.transactionId,
      code: `${code}changed`,
      state: login.state
    })).rejects.toThrow("Login transaction expired");
    await store.close();
  });

  it("expires the callback receipt before the longer-lived session", async () => {
    const store = service(undefined, { receiptTtlMs: 40, sessionTtlMs: 1_000 });
    const { login, code } = await loginAndCode(store);
    const sessionId = await store.callback({ transactionId: login.transactionId, code, state: login.state });
    await new Promise((resolve) => setTimeout(resolve, 80));
    await expect(store.callback({ transactionId: login.transactionId, code, state: login.state }))
      .rejects.toThrow("Login transaction expired");
    expect(await store.getSession(sessionId)).not.toBeNull();
    await store.close();
  });

  it("fails closed when either half of the session-vault pair is orphaned", async () => {
    const suffix = `orphan-${++sequence}`;
    const store = service(suffix);
    const { login, code } = await loginAndCode(store);
    const sessionId = await store.callback({ transactionId: login.transactionId, code, state: login.state });
    await isolated.client.del(
      `appsell:test:${suffix}:{appsell:test:${suffix}}:vault:${sha256(sessionId)}`
    );
    await expect(store.getSession(sessionId)).rejects.toBeInstanceOf(OAuthVaultIntegrityError);
    await expect(store.callback({ transactionId: login.transactionId, code, state: login.state }))
      .rejects.toBeInstanceOf(OAuthVaultIntegrityError);
    await store.close();
  });

  it("authenticates the existing vault before accepting an idempotent callback retry", async () => {
    const suffix = `retry-tamper-${++sequence}`;
    const store = service(suffix);
    const { login, code } = await loginAndCode(store);
    const sessionId = await store.callback({ transactionId: login.transactionId, code, state: login.state });
    const vaultKey = `appsell:test:${suffix}:{appsell:test:${suffix}}:vault:${sha256(sessionId)}`;
    const envelope = JSON.parse((await isolated.client.get(vaultKey))!);
    const ciphertext = envelope.payload.ct as string;
    envelope.payload.ct = `${ciphertext.startsWith("A") ? "B" : "A"}${ciphertext.slice(1)}`;
    await isolated.client.set(vaultKey, JSON.stringify(envelope), "PX", 1_000);
    await expect(store.callback({ transactionId: login.transactionId, code, state: login.state }))
      .rejects.toBeInstanceOf(OAuthVaultIntegrityError);
    await store.close();
  });

  it("rejects a vault envelope swapped between two sessions by authenticated context", async () => {
    const suffix = `swap-${++sequence}`;
    const store = service(suffix);
    const firstFlow = await loginAndCode(store);
    const firstSession = await store.callback({
      transactionId: firstFlow.login.transactionId,
      code: firstFlow.code,
      state: firstFlow.login.state
    });
    const secondFlow = await loginAndCode(store);
    const secondSession = await store.callback({
      transactionId: secondFlow.login.transactionId,
      code: secondFlow.code,
      state: secondFlow.login.state
    });
    const prefix = `appsell:test:${suffix}:{appsell:test:${suffix}}:vault:`;
    const firstKey = `${prefix}${sha256(firstSession)}`;
    const secondKey = `${prefix}${sha256(secondSession)}`;
    const [firstEnvelope, secondEnvelope] = await isolated.client.mget(firstKey, secondKey);
    expect(firstEnvelope).not.toBeNull();
    expect(secondEnvelope).not.toBeNull();
    await isolated.client.mset(firstKey, secondEnvelope!, secondKey, firstEnvelope!);
    await expect(store.getSession(firstSession)).rejects.toBeInstanceOf(OAuthVaultIntegrityError);
    await expect(store.getSession(secondSession)).rejects.toBeInstanceOf(OAuthVaultIntegrityError);
    await store.close();
  });

  it("does not create a partial session when the vault key already exists", async () => {
    const suffix = `collision-${++sequence}`;
    const prefix = `appsell:test:${suffix}`;
    const store = service(suffix);
    const { login, code } = await loginAndCode(store);
    const sessionId = hmacSha256(sessionHmacKey, login.transactionId);
    const scope = `${prefix}:{${prefix}}`;
    const sessionKey = `${scope}:session:${sha256(sessionId)}`;
    const vaultRecordKey = `${scope}:vault:${sha256(sessionId)}`;
    await isolated.client.psetex(vaultRecordKey, 1_000, "occupied");
    await expect(store.callback({ transactionId: login.transactionId, code, state: login.state }))
      .rejects.toThrow("Could not create session vault");
    expect(await isolated.client.get(sessionKey)).toBeNull();
    await store.close();
  });

  it("rejects a tampered authorization-code subject before creating a session", async () => {
    const suffix = `subject-${++sequence}`;
    const prefix = `appsell:test:${suffix}`;
    const store = service(suffix);
    const { login, code } = await loginAndCode(store);
    const scope = `${prefix}:{${prefix}}`;
    const codeKey = `${scope}:code:${sha256(code)}`;
    const codeRecord = JSON.parse((await isolated.client.get(codeKey))!);
    codeRecord.subject = "attacker-subject";
    await isolated.client.set(codeKey, JSON.stringify(codeRecord), "PX", 1_000);
    await expect(store.callback({ transactionId: login.transactionId, code, state: login.state }))
      .rejects.toThrow("OAuth authorization code integrity failure");
    const sessionId = hmacSha256(sessionHmacKey, login.transactionId);
    expect(await isolated.client.get(`${scope}:session:${sha256(sessionId)}`)).toBeNull();
    expect(await isolated.client.get(`${scope}:vault:${sha256(sessionId)}`)).toBeNull();
    await store.close();
  });
});

describe("Redis OAuth configuration", () => {
  it("requires both an explicit URL and a unique AppSell namespace", () => {
    expect(() => RedisOAuthService.fromEnvironment({})).toThrow(OAuthStoreConfigurationError);
    expect(() => RedisOAuthService.fromEnvironment({
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell",
      APPSELL_SESSION_HMAC_KEY_B64: sessionHmacKey.toString("base64"),
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: environmentKeyring()
    })).toThrow("appsell:<environment>:<instance>");
    const configured = RedisOAuthService.fromEnvironment({
      NODE_ENV: "test",
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell:test:unique-issue14",
      APPSELL_SESSION_HMAC_KEY_B64: sessionHmacKey.toString("base64"),
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: environmentKeyring()
    });
    expect(configured).toBeInstanceOf(RedisOAuthService);
    void configured.close();
    expect(() => RedisOAuthService.fromEnvironment({
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell:test:unique-issue14",
      APPSELL_SESSION_HMAC_KEY_B64: "not-a-key"
    })).toThrow("canonical base64");
    expect(() => RedisOAuthService.fromEnvironment({
      NODE_ENV: "test",
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell:test:unique-issue17",
      APPSELL_SESSION_HMAC_KEY_B64: sessionHmacKey.toString("base64"),
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
        { id: "reused", key: sessionHmacKey.toString("base64"), active: true }
      ])
    })).toThrow(OAuthVaultConfigurationError);
    const reusedVault = new EnvelopeTokenVault(new LocalAesKeyEnvelopeProvider([
      { id: "reused-direct", key: sessionHmacKey, active: true }
    ], { nodeEnvironment: "test" }));
    expect(() => new RedisOAuthService({
      redisUrl: "redis://127.0.0.1:6380",
      prefix: "appsell:test:direct-reuse",
      sessionHmacKey,
      tokenVault: reusedVault
    })).toThrow(OAuthVaultConfigurationError);
    expect(() => RedisOAuthService.fromEnvironment({
      NODE_ENV: "production",
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell:test:unique-issue17",
      APPSELL_SESSION_HMAC_KEY_B64: sessionHmacKey.toString("base64"),
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: environmentKeyring()
    })).toThrow(OAuthVaultConfigurationError);
  });
});

function environmentKeyring() {
  return JSON.stringify([{ id: "local-v1", key: vaultKey.toString("base64"), active: true }]);
}
