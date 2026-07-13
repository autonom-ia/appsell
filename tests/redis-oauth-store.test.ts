import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { hmacSha256, sha256 } from "../lib/crypto";
import {
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
  it("creates one session and returns the same id for 20 concurrent callback retries", async () => {
    const store = service();
    const peer = new RedisOAuthService({
      redisUrl: isolated.url,
      prefix: "appsell:test:case-1",
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
    const sessionKeys = await isolated.client.keys(`appsell:test:case-1:{appsell:test:case-1}:session:*`);
    expect(sessionKeys).toHaveLength(1);
    const vaultKeys = await isolated.client.keys(`appsell:test:case-1:{appsell:test:case-1}:vault:*`);
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
    const [sessionTtl, vaultTtl] = await Promise.all([
      isolated.client.pttl(`${activePrefix}:session:${sha256(sessionId)}`),
      isolated.client.pttl(`${activePrefix}:vault:${sha256(sessionId)}`)
    ]);
    expect(sessionTtl).toBeGreaterThan(0);
    expect(Math.abs(sessionTtl - vaultTtl)).toBeLessThanOrEqual(5);
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
