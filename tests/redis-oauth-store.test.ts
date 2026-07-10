import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { sha256 } from "../lib/crypto";
import {
  OAuthStoreConfigurationError,
  OAuthStoreUnavailableError,
  RedisOAuthService
} from "../lib/redis-oauth-service";
import { startIsolatedRedis, type IsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

let isolated: IsolatedRedis;
let sequence = 0;
const sessionHmacKey = Buffer.alloc(32, 7);

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
    ...overrides
  });
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
    const { login, code } = await loginAndCode(store);
    const attempts = await Promise.allSettled(Array.from({ length: 20 }, () => store.callback({
      transactionId: login.transactionId,
      code,
      state: login.state
    })));
    expect(attempts.filter((attempt) => attempt.status === "fulfilled")).toHaveLength(20);
    const sessionIds = attempts.flatMap((attempt) => attempt.status === "fulfilled" ? [attempt.value] : []);
    expect(new Set(sessionIds)).toHaveLength(1);
    const sessionKeys = await isolated.client.keys(`appsell:test:case-1:session:*`);
    expect(sessionKeys).toHaveLength(1);
    await store.close();
  });

  it("shares transactions and sessions between independent service instances", async () => {
    const prefix = `appsell:test:shared-${++sequence}`;
    const first = new RedisOAuthService({ redisUrl: isolated.url, prefix, sessionHmacKey });
    const second = new RedisOAuthService({ redisUrl: isolated.url, prefix, sessionHmacKey });
    const login = await first.beginLogin();
    const code = await second.authorize({ state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge });
    const sessionId = await second.callback({ transactionId: login.transactionId, code, state: login.state });
    expect(await first.getSession(sessionId)).toMatchObject({ subject: "dev-user-001" });
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

    const keys = await isolated.client.keys(`appsell:test:${suffix}-first:*`);
    const values = await Promise.all(keys.map((key) => isolated.client.get(key)));
    const persisted = [...keys, ...values.filter((value): value is string => value !== null)].join("\n");
    for (const opaque of [login.transactionId, code, login.state, sessionId]) expect(persisted).not.toContain(opaque);
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
});

describe("Redis OAuth configuration", () => {
  it("requires both an explicit URL and a unique AppSell namespace", () => {
    expect(() => RedisOAuthService.fromEnvironment({})).toThrow(OAuthStoreConfigurationError);
    expect(() => RedisOAuthService.fromEnvironment({
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell",
      APPSELL_SESSION_HMAC_KEY_B64: sessionHmacKey.toString("base64")
    })).toThrow("appsell:<environment>:<instance>");
    const configured = RedisOAuthService.fromEnvironment({
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell:test:unique-issue14",
      APPSELL_SESSION_HMAC_KEY_B64: sessionHmacKey.toString("base64")
    });
    expect(configured).toBeInstanceOf(RedisOAuthService);
    void configured.close();
    expect(() => RedisOAuthService.fromEnvironment({
      APPSELL_REDIS_URL: "redis://127.0.0.1:6380",
      APPSELL_REDIS_PREFIX: "appsell:test:unique-issue14",
      APPSELL_SESSION_HMAC_KEY_B64: "not-a-key"
    })).toThrow("canonical base64");
  });
});
