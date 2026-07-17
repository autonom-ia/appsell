import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { hmacSha256, sha256 } from "../lib/crypto";
import { OpenBaoTransitKeyEnvelopeProvider } from "../lib/openbao-transit-key-envelope-provider.server";
import { OAuthVaultIntegrityError, RedisOAuthService } from "../lib/redis-oauth-service";
import { EnvelopeTokenVault } from "../lib/token-vault";
import { startIsolatedRedis, type IsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

let isolated: IsolatedRedis;

beforeAll(async () => {
  isolated = await startIsolatedRedis();
}, 10_000);

afterAll(async () => {
  if (isolated) await isolated.stop();
});

describe("OpenBao Transit BFF failure boundary", () => {
  it("does not persist a partial session or vault when Transit is unavailable after Identity exchange", async () => {
    const deadPort = await unusedNonDefaultPort();
    const sessionHmacKey = Buffer.alloc(32, 81);
    const prefix = "appsell:test:transit-unavailable";
    const provider = new OpenBaoTransitKeyEnvelopeProvider({
      address: `http://127.0.0.1:${deadPort}`,
      mount: "appsell-transit",
      keyName: "appsell-oauth-vault",
      token: "runtime-token-with-minimum-length",
      timeoutMs: 100,
      nodeEnvironment: "test"
    });
    const service = new RedisOAuthService({
      redisUrl: isolated.url,
      prefix,
      sessionHmacKey,
      tokenVault: new EnvelopeTokenVault(provider),
      identityCallbackLeaseTtlMs: 250,
      identityCallbackWaitTimeoutMs: 500,
      identityCallbackPollIntervalMs: 10
    });
    const login = await service.beginLogin("identity-client");
    const sessionId = hmacSha256(sessionHmacKey, login.transactionId);
    const now = Date.now();
    await expect(service.callbackIdentity({
      transactionId: login.transactionId,
      code: "c".repeat(43),
      state: login.state
    }, {
      async exchangeAuthorizationCode() {
        return {
          accessToken: "verified-access-token",
          refreshToken: "r".repeat(43),
          tokenType: "Bearer" as const,
          expiresAt: now + 60_000,
          refreshTokenExpiresAt: now + 300_000,
          identity: {
            issuer: "https://identity.example.test",
            sub: "identity-user-001",
            email: "buyer.demo@autonomia.site",
            emailVerified: true as const
          }
        };
      }
    })).rejects.toBeInstanceOf(OAuthVaultIntegrityError);

    const scope = `${prefix}:{${prefix}}`;
    expect(await isolated.client.get(`${scope}:session:${sha256(sessionId)}`)).toBeNull();
    expect(await isolated.client.get(`${scope}:vault:${sha256(sessionId)}`)).toBeNull();
    await service.close();
  });
});
