import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { sha256 } from "../lib/crypto";
import type { OAuthTokenRefresher, OAuthTokenRefreshSuccess } from "../lib/oauth-refresh";
import {
  OAuthRefreshAmbiguousError,
  OAuthRefreshInvalidGrantError,
  OAuthRefreshRevisionError,
  OAuthRefreshUnavailableError,
  OAuthStoreUnavailableError,
  OAuthVaultIntegrityError,
  RedisOAuthService,
  type RedisOAuthOptions
} from "../lib/redis-oauth-service";
import { EnvelopeTokenVault, LocalAesKeyEnvelopeProvider, type TokenBundle } from "../lib/token-vault";
import { startIsolatedRedis, type IsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

let isolated: IsolatedRedis;
let sequence = 0;
const sessionHmacKey = Buffer.alloc(32, 31);
const vaultKey = Buffer.alloc(32, 47);

beforeAll(async () => {
  isolated = await startIsolatedRedis();
}, 10_000);

afterAll(async () => {
  if (isolated) await isolated.stop();
});

function testTokenVault() {
  return new EnvelopeTokenVault(new LocalAesKeyEnvelopeProvider([
    { id: "refresh-v1", key: vaultKey, active: true }
  ], { nodeEnvironment: "test", forbiddenKeys: [sessionHmacKey] }));
}

function createService(prefix: string, overrides: Partial<RedisOAuthOptions> = {}) {
  return new RedisOAuthService({
    redisUrl: isolated.url,
    prefix,
    sessionHmacKey,
    tokenVault: testTokenVault(),
    refreshLeaseTtlMs: 500,
    refreshProviderTimeoutMs: 250,
    refreshWaitTimeoutMs: 2_000,
    refreshPollIntervalMs: 5,
    ...overrides
  });
}

async function createSession(prefix = `appsell:test:refresh-${++sequence}`) {
  const first = createService(prefix);
  const second = createService(prefix);
  const login = await first.beginLogin("refresh-test");
  const code = await second.authorize({
    state: login.state,
    nonce: login.nonce,
    codeChallenge: login.codeChallenge
  }, "refresh-test");
  const sessionId = await first.callback({ transactionId: login.transactionId, code, state: login.state });
  const initial = await first.getServerTokenBundle(sessionId);
  expect(initial?.revision).toBe(1);
  return { prefix, first, second, sessionId, initial: initial! };
}

function successFor(bundle: TokenBundle, suffix = "next"): OAuthTokenRefreshSuccess {
  const now = Date.now();
  return {
    kind: "success",
    accessToken: `${bundle.accessToken}-${suffix}`,
    refreshToken: `${bundle.refreshToken}-${suffix}`,
    accessTokenExpiresAt: now + 30_000,
    refreshTokenExpiresAt: now + 60_000,
    scopes: [...bundle.scopes]
  };
}

function key(prefix: string, kind: string, sessionId: string) {
  return `${prefix}:{${prefix}}:${kind}:${sha256(sessionId)}`;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

interface RedisEvalClient {
  eval(...args: unknown[]): Promise<unknown>;
}

function internalRedis(service: RedisOAuthService) {
  return (service as unknown as { redis: RedisEvalClient }).redis;
}

describe("distributed Redis token refresh", () => {
  it("singleflights 50 callers across two independent connections", async () => {
    const context = await createSession();
    let calls = 0;
    const operationIds = new Set<string>();
    const refresher: OAuthTokenRefresher = {
      async refresh(input) {
        calls += 1;
        operationIds.add(input.idempotencyKey);
        await new Promise((resolve) => setTimeout(resolve, 25));
        return successFor(context.initial);
      }
    };

    const results = await Promise.all(Array.from({ length: 50 }, (_, index) =>
      (index % 2 ? context.first : context.second).refreshSession(context.sessionId, 1, refresher)
    ));
    expect(calls).toBe(1);
    expect(operationIds.size).toBe(1);
    expect(new Set(results.map((bundle) => bundle.accessToken)).size).toBe(1);
    expect(results.every((bundle) => bundle.revision === 2)).toBe(true);
    expect(await context.first.getServerTokenBundle(context.sessionId)).toEqual(results[0]);

    await context.first.close();
    await context.second.close();
  });

  it("keeps old and rotated tokens out of Redis keys and plaintext values", async () => {
    const context = await createSession();
    const rotated = await context.first.refreshSession(context.sessionId, 1, {
      async refresh() {
        return successFor(context.initial, "secret-rotated");
      }
    });
    const keys = await isolated.client.keys(`${context.prefix}:{${context.prefix}}:*`);
    const values = await Promise.all(keys.map((redisKey) => isolated.client.get(redisKey)));
    const persisted = [...keys, ...values.filter((value): value is string => value !== null)].join("\n");
    for (const secret of [
      context.initial.accessToken,
      context.initial.refreshToken,
      rotated.accessToken,
      rotated.refreshToken,
      context.sessionId
    ]) {
      expect(persisted).not.toContain(secret);
    }

    await context.first.close();
    await context.second.close();
  });

  it("returns the committed winner after the Redis commit response is actually lost", async () => {
    const context = await createSession();
    let calls = 0;
    const redis = internalRedis(context.first);
    const originalEval = redis.eval.bind(redis);
    let injected = false;
    redis.eval = async (...args: unknown[]) => {
      const result = await originalEval(...args);
      if (!injected && typeof args[0] === "string" && args[0].includes("local next_session = cjson.decode")) {
        injected = true;
        throw new Error("injected lost Redis response after commit");
      }
      return result;
    };
    await expect(context.first.refreshSession(context.sessionId, 1, {
      async refresh() {
        calls += 1;
        return successFor(context.initial);
      }
    })).rejects.toBeInstanceOf(OAuthStoreUnavailableError);
    redis.eval = originalEval;

    const retry = await context.second.refreshSession(context.sessionId, 1, {
      async refresh() {
        calls += 1;
        return successFor(context.initial, "must-not-run");
      }
    });
    expect(injected).toBe(true);
    expect(calls).toBe(1);
    expect(retry.revision).toBe(2);
    expect(retry).toEqual(await context.first.getServerTokenBundle(context.sessionId));

    await context.first.close();
    await context.second.close();
  });

  it("reuses the persisted operation ID after a transient result and never advances the revision early", async () => {
    const context = await createSession();
    const operationIds: string[] = [];
    await expect(context.first.refreshSession(context.sessionId, 1, {
      async refresh(input) {
        operationIds.push(input.idempotencyKey);
        return { kind: "transient" };
      }
    })).rejects.toBeInstanceOf(OAuthRefreshUnavailableError);
    expect((await context.first.getServerTokenBundle(context.sessionId))?.revision).toBe(1);

    const winner = await context.second.refreshSession(context.sessionId, 1, {
      async refresh(input) {
        operationIds.push(input.idempotencyKey);
        return successFor(context.initial);
      }
    });
    expect(operationIds).toHaveLength(2);
    expect(new Set(operationIds).size).toBe(1);
    expect(winner.revision).toBe(2);

    await context.first.close();
    await context.second.close();
  });

  it("recovers a prepared intent left by a crash before the provider started", async () => {
    const context = await createSession();
    const operationId = "A".repeat(32);
    await isolated.client.psetex(
      key(context.prefix, "refresh-intent", context.sessionId),
      5_000,
      `1:${operationId}:prepared`
    );
    let observedOperationId = "";
    const winner = await context.second.refreshSession(context.sessionId, 1, {
      async refresh(input) {
        observedOperationId = input.idempotencyKey;
        return successFor(context.initial, "prepared-recovery");
      }
    });
    expect(observedOperationId).toBe(operationId);
    expect(winner.revision).toBe(2);

    await context.first.close();
    await context.second.close();
  });

  it("persists an ambiguous upstream outcome and does not retry the provider", async () => {
    const context = await createSession();
    let calls = 0;
    const ambiguous: OAuthTokenRefresher = {
      async refresh() {
        calls += 1;
        return { kind: "ambiguous" };
      }
    };
    await expect(context.first.refreshSession(context.sessionId, 1, ambiguous))
      .rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);
    await expect(context.second.refreshSession(context.sessionId, 1, ambiguous))
      .rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);
    expect(calls).toBe(1);
    expect((await context.first.getServerTokenBundle(context.sessionId))?.revision).toBe(1);

    await context.first.close();
    await context.second.close();
  });

  it("turns an inflight crash gap into ambiguity and rejects a late stale success", async () => {
    const context = await createSession();
    const entered = deferred<void>();
    const release = deferred<OAuthTokenRefreshSuccess>();
    let calls = 0;
    const pending = context.first.refreshSession(context.sessionId, 1, {
      async refresh() {
        calls += 1;
        entered.resolve();
        return release.promise;
      }
    });
    await entered.promise;
    await isolated.client.del(key(context.prefix, "refresh-lease", context.sessionId));
    await expect(context.second.refreshSession(context.sessionId, 1, {
      async refresh() {
        calls += 1;
        return successFor(context.initial, "must-not-run");
      }
    })).rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);
    release.resolve(successFor(context.initial, "stale-success"));
    await expect(pending).rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);
    expect(calls).toBe(1);

    await context.first.close();
    await context.second.close();
  });

  it("revokes only the current generation on invalid_grant", async () => {
    const context = await createSession();
    await expect(context.first.refreshSession(context.sessionId, 1, {
      async refresh() {
        return { kind: "invalid_grant" };
      }
    })).rejects.toBeInstanceOf(OAuthRefreshInvalidGrantError);
    expect(await context.first.getSession(context.sessionId)).toBeNull();
    expect(await context.first.getServerTokenBundle(context.sessionId)).toBeNull();

    await context.first.close();
    await context.second.close();
  });

  it("prevents a stale invalid_grant owner from deleting a newer fenced winner", async () => {
    const context = await createSession();
    const entered = deferred<void>();
    const release = deferred<{ kind: "invalid_grant" }>();
    const stale = context.first.refreshSession(context.sessionId, 1, {
      async refresh() {
        entered.resolve();
        return release.promise;
      }
    });
    await entered.promise;
    const intentKey = key(context.prefix, "refresh-intent", context.sessionId);
    const intent = await isolated.client.get(intentKey);
    expect(intent).toMatch(/:inflight$/);
    await isolated.client.psetex(intentKey, 5_000, intent!.replace(/:inflight$/, ":retryable"));
    await isolated.client.del(key(context.prefix, "refresh-lease", context.sessionId));

    const winner = await context.second.refreshSession(context.sessionId, 1, {
      async refresh() {
        return successFor(context.initial, "winner");
      }
    });
    release.resolve({ kind: "invalid_grant" });
    expect(await stale).toEqual(winner);
    expect((await context.first.getServerTokenBundle(context.sessionId))?.revision).toBe(2);

    await context.first.close();
    await context.second.close();
  });

  it("does not resurrect a session logged out while the provider is pending", async () => {
    const context = await createSession();
    const entered = deferred<void>();
    const release = deferred<OAuthTokenRefreshSuccess>();
    const pending = context.first.refreshSession(context.sessionId, 1, {
      async refresh() {
        entered.resolve();
        return release.promise;
      }
    });
    await entered.promise;
    await context.second.logout(context.sessionId);
    release.resolve(successFor(context.initial));
    await expect(pending).rejects.toBeInstanceOf(OAuthRefreshUnavailableError);

    for (const kind of ["session", "vault", "refresh-lease", "refresh-intent", "refresh-fence"]) {
      expect(await isolated.client.get(key(context.prefix, kind, context.sessionId))).toBeNull();
    }

    await context.first.close();
    await context.second.close();
  });

  it("fails before provider side effects for orphaned, revision-divergent and unavailable stores", async () => {
    const orphan = await createSession();
    let calls = 0;
    const refresher: OAuthTokenRefresher = {
      async refresh() {
        calls += 1;
        return successFor(orphan.initial);
      }
    };
    await isolated.client.del(key(orphan.prefix, "vault", orphan.sessionId));
    await expect(orphan.first.refreshSession(orphan.sessionId, 1, refresher))
      .rejects.toBeInstanceOf(OAuthVaultIntegrityError);

    const divergent = await createSession();
    const sessionKey = key(divergent.prefix, "session", divergent.sessionId);
    const record = JSON.parse((await isolated.client.get(sessionKey))!);
    record.tokenRevision = 2;
    await isolated.client.set(sessionKey, JSON.stringify(record), "PX", 5_000);
    await expect(divergent.first.refreshSession(divergent.sessionId, 1, refresher))
      .rejects.toBeInstanceOf(OAuthVaultIntegrityError);

    const port = await unusedNonDefaultPort();
    const offline = new RedisOAuthService({
      redisUrl: `redis://127.0.0.1:${port}/0`,
      prefix: `appsell:test:refresh-offline-${++sequence}`,
      sessionHmacKey,
      tokenVault: testTokenVault(),
      connectTimeoutMs: 100
    });
    await expect(offline.refreshSession("opaque-session", 1, refresher))
      .rejects.toBeInstanceOf(OAuthStoreUnavailableError);
    expect(calls).toBe(0);

    await orphan.first.close();
    await orphan.second.close();
    await divergent.first.close();
    await divergent.second.close();
    await offline.close();
  });

  it("keeps fence values exact above 2^53 and fails closed on Redis integer overflow", async () => {
    const precise = await createSession();
    const fenceKey = key(precise.prefix, "refresh-fence", precise.sessionId);
    await isolated.client.set(fenceKey, "9007199254740992", "PX", 5_000);
    await precise.first.refreshSession(precise.sessionId, 1, {
      async refresh() {
        return successFor(precise.initial);
      }
    });
    expect(await isolated.client.get(fenceKey)).toBe("9007199254740993");

    const overflow = await createSession();
    let overflowCalls = 0;
    await isolated.client.set(
      key(overflow.prefix, "refresh-fence", overflow.sessionId),
      "9223372036854775807",
      "PX",
      5_000
    );
    await expect(overflow.first.refreshSession(overflow.sessionId, 1, {
      async refresh() {
        overflowCalls += 1;
        return successFor(overflow.initial);
      }
    })).rejects.toBeInstanceOf(OAuthStoreUnavailableError);
    expect(overflowCalls).toBe(0);

    await precise.first.close();
    await precise.second.close();
    await overflow.first.close();
    await overflow.second.close();
  });

  it("never expands pair TTL and rejects persistent pair halves before the provider", async () => {
    const context = await createSession();
    const sessionKey = key(context.prefix, "session", context.sessionId);
    const vaultRecordKey = key(context.prefix, "vault", context.sessionId);
    await isolated.client.pexpire(sessionKey, 1_500);
    await isolated.client.pexpire(vaultRecordKey, 700);
    const before = Math.min(
      await isolated.client.pttl(sessionKey),
      await isolated.client.pttl(vaultRecordKey)
    );
    await context.first.refreshSession(context.sessionId, 1, {
      async refresh() {
        return successFor(context.initial);
      }
    });
    const sessionTtl = await isolated.client.pttl(sessionKey);
    const vaultTtl = await isolated.client.pttl(vaultRecordKey);
    expect(sessionTtl).toBeGreaterThan(0);
    expect(vaultTtl).toBeGreaterThan(0);
    expect(sessionTtl).toBeLessThanOrEqual(before);
    expect(vaultTtl).toBeLessThanOrEqual(before);
    expect(Math.abs(sessionTtl - vaultTtl)).toBeLessThanOrEqual(5);

    const persistent = await createSession();
    let calls = 0;
    await isolated.client.persist(key(persistent.prefix, "vault", persistent.sessionId));
    await expect(persistent.first.refreshSession(persistent.sessionId, 1, {
      async refresh() {
        calls += 1;
        return successFor(persistent.initial);
      }
    })).rejects.toBeInstanceOf(OAuthVaultIntegrityError);
    expect(calls).toBe(0);

    await context.first.close();
    await context.second.close();
    await persistent.first.close();
    await persistent.second.close();
  });

  it("caps TTL by session absolute expiry and by the new refresh token expiry", async () => {
    const absolute = await createSession();
    const absoluteSessionKey = key(absolute.prefix, "session", absolute.sessionId);
    const absoluteVaultKey = key(absolute.prefix, "vault", absolute.sessionId);
    const session = JSON.parse((await isolated.client.get(absoluteSessionKey))!);
    session.expiresAt = Date.now() + 300;
    await isolated.client.set(absoluteSessionKey, JSON.stringify(session), "PX", 2_000);
    await isolated.client.pexpire(absoluteVaultKey, 2_000);
    await absolute.first.refreshSession(absolute.sessionId, 1, {
      async refresh() {
        return successFor(absolute.initial, "absolute");
      }
    });
    expect(await isolated.client.pttl(absoluteSessionKey)).toBeLessThanOrEqual(300);
    expect(await isolated.client.pttl(absoluteVaultKey)).toBeLessThanOrEqual(300);

    const refreshExpiry = await createSession();
    const refreshSessionKey = key(refreshExpiry.prefix, "session", refreshExpiry.sessionId);
    const refreshVaultKey = key(refreshExpiry.prefix, "vault", refreshExpiry.sessionId);
    await refreshExpiry.first.refreshSession(refreshExpiry.sessionId, 1, {
      async refresh() {
        const now = Date.now();
        return {
          ...successFor(refreshExpiry.initial, "expiry"),
          accessTokenExpiresAt: now + 100,
          refreshTokenExpiresAt: now + 250
        };
      }
    });
    expect(await isolated.client.pttl(refreshSessionKey)).toBeLessThanOrEqual(250);
    expect(await isolated.client.pttl(refreshVaultKey)).toBeLessThanOrEqual(250);

    await absolute.first.close();
    await absolute.second.close();
    await refreshExpiry.first.close();
    await refreshExpiry.second.close();
  });

  it("rejects invalid, future and terminal revisions without invoking the provider", async () => {
    const context = await createSession();
    let calls = 0;
    const refresher: OAuthTokenRefresher = {
      async refresh() {
        calls += 1;
        return successFor(context.initial);
      }
    };
    for (const revision of [0, -1, 2, Number.MAX_SAFE_INTEGER]) {
      await expect(context.first.refreshSession(context.sessionId, revision, refresher))
        .rejects.toBeInstanceOf(OAuthRefreshRevisionError);
    }
    expect(calls).toBe(0);

    await context.first.close();
    await context.second.close();
  });

  it("aborts provider timeout and treats throws, malformed output and scope amplification as ambiguous", async () => {
    const timeout = await createSession();
    let timeoutSignal: AbortSignal | undefined;
    await expect(timeout.first.refreshSession(timeout.sessionId, 1, {
      async refresh(input) {
        timeoutSignal = input.signal;
        return new Promise(() => undefined);
      }
    })).rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);
    expect(timeoutSignal?.aborted).toBe(true);

    const thrown = await createSession();
    await expect(thrown.first.refreshSession(thrown.sessionId, 1, {
      async refresh() {
        throw new Error("provider transport failure");
      }
    })).rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);

    const malformed = await createSession();
    await expect(malformed.first.refreshSession(malformed.sessionId, 1, {
      async refresh() {
        return { kind: "success", accessToken: "incomplete" } as never;
      }
    })).rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);

    const oversized = await createSession();
    await expect(oversized.first.refreshSession(oversized.sessionId, 1, {
      async refresh() {
        return {
          ...successFor(oversized.initial),
          accessToken: "x".repeat(16 * 1024 + 1),
          scopes: Array.from({ length: 65 }, (_, index) => `scope-${index}`)
        };
      }
    })).rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);

    const scopeAmplification = await createSession();
    await expect(scopeAmplification.first.refreshSession(scopeAmplification.sessionId, 1, {
      async refresh() {
        return {
          ...successFor(scopeAmplification.initial),
          scopes: [...scopeAmplification.initial.scopes, "admin"]
        };
      }
    })).rejects.toBeInstanceOf(OAuthRefreshAmbiguousError);
    expect((await scopeAmplification.first.getServerTokenBundle(scopeAmplification.sessionId))?.revision).toBe(1);

    await timeout.first.close();
    await timeout.second.close();
    await thrown.first.close();
    await thrown.second.close();
    await malformed.first.close();
    await malformed.second.close();
    await oversized.first.close();
    await oversized.second.close();
    await scopeAmplification.first.close();
    await scopeAmplification.second.close();
  });
});
