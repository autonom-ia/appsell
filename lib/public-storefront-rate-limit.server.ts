import "server-only";

import { createHash } from "node:crypto";
import { isIP } from "node:net";
import Redis from "ioredis";
import { isPublicSlug } from "./public-storefront";

const WINDOW_MS = 60_000;
const MAX_PER_CLIENT = 60;
// Launch envelope: 1,000 buyers across 100 sellers, four actual navigations each,
// plus 50% global headroom. A single seller is capped separately so it cannot
// consume the shared launch budget.
const MAX_PER_SELLER = 300;
const MAX_GLOBAL = 6_000;
const PREFIX = /^appsell:[a-z0-9][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9_-]{5,63}$/;

const SCRIPT = `
local global_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local seller_count = tonumber(redis.call('GET', KEYS[2]) or '0')
local client_count = tonumber(redis.call('GET', KEYS[3]) or '0')
if global_count >= tonumber(ARGV[1]) or seller_count >= tonumber(ARGV[2]) or client_count >= tonumber(ARGV[3]) then return 0 end
global_count = redis.call('INCR', KEYS[1])
seller_count = redis.call('INCR', KEYS[2])
client_count = redis.call('INCR', KEYS[3])
if global_count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[4]) end
if seller_count == 1 then redis.call('PEXPIRE', KEYS[2], ARGV[4]) end
if client_count == 1 then redis.call('PEXPIRE', KEYS[3], ARGV[4]) end
return 1
`;

export class PublicStorefrontRateLimitError extends Error {
  constructor(readonly code: "rate_limited" | "rate_limit_unavailable") {
    super(code);
  }
}

class PublicStorefrontRateLimiter {
  private redis: Redis | undefined;
  private connectPromise: Promise<Redis> | undefined;

  constructor(private readonly prefix: string, private readonly redisUrl: string) {
    if (!PREFIX.test(prefix) || !isRedisUrl(redisUrl)) {
      throw new PublicStorefrontRateLimitError("rate_limit_unavailable");
    }
  }

  async assertAllowed(headers: Headers, pathname: string) {
    try {
      const redis = await this.ready();
      const window = Math.floor(Date.now() / WINDOW_MS);
      const scope = `${this.prefix}:{${this.prefix}}:storefront-rate`;
      const client = clientFingerprint(headers);
      const seller = sellerFingerprint(pathname);
      const result = await redis.eval(
        SCRIPT,
        3,
        `${scope}:global:${window}`,
        `${scope}:seller:${seller}:${window}`,
        `${scope}:client:${client.digest}:${window}`,
        String(MAX_GLOBAL),
        String(MAX_PER_SELLER),
        String(client.trusted ? MAX_PER_CLIENT : MAX_GLOBAL),
        String(WINDOW_MS + 1_000)
      );
      if (result !== 1) throw new PublicStorefrontRateLimitError("rate_limited");
    } catch (error) {
      if (error instanceof PublicStorefrontRateLimitError) throw error;
      this.resetRedis();
      throw new PublicStorefrontRateLimitError("rate_limit_unavailable");
    }
  }

  async close() {
    const redis = this.redis;
    this.redis = undefined;
    this.connectPromise = undefined;
    if (!redis) return;
    if (redis.status === "ready") await redis.quit();
    else redis.disconnect();
  }

  private async ready(): Promise<Redis> {
    if (this.redis?.status === "ready") return this.redis;
    if (this.connectPromise) return this.connectPromise;

    this.resetRedis();
    const redis = this.createRedis();
    this.redis = redis;
    const pending = redis.connect().then(() => {
      if (redis.status !== "ready") throw new Error("Redis did not become ready.");
      return redis;
    }).catch((error) => {
      if (this.redis === redis) this.resetRedis();
      throw error;
    }).finally(() => {
      if (this.connectPromise === pending) this.connectPromise = undefined;
    });
    this.connectPromise = pending;
    return pending;
  }

  private createRedis() {
    const redis = new Redis(this.redisUrl, {
      lazyConnect: true,
      enableOfflineQueue: false,
      maxRetriesPerRequest: 1,
      connectTimeout: 1_000,
      commandTimeout: 1_000,
      retryStrategy: () => null
    });
    redis.on("error", () => {
      // Transport failures are translated to a non-sensitive 503 response.
    });
    return redis;
  }

  private resetRedis() {
    this.redis?.disconnect();
    this.redis = undefined;
  }
}

declare global {
  var __appsellPublicStorefrontRateLimiter: PublicStorefrontRateLimiter | undefined;
}

export async function assertPublicStorefrontRateLimit(headers: Headers, pathname: string) {
  globalThis.__appsellPublicStorefrontRateLimiter ??= new PublicStorefrontRateLimiter(
    process.env.APPSELL_REDIS_PREFIX ?? "",
    process.env.APPSELL_REDIS_URL ?? ""
  );
  await globalThis.__appsellPublicStorefrontRateLimiter.assertAllowed(headers, pathname);
}

export async function closePublicStorefrontRateLimiterForTest() {
  await globalThis.__appsellPublicStorefrontRateLimiter?.close();
  globalThis.__appsellPublicStorefrontRateLimiter = undefined;
}

function clientFingerprint(headers: Headers) {
  const trusted = process.env.APPSELL_TRUST_PROXY_HEADERS === "true";
  const forwarded = trusted ? headers.get("x-forwarded-for")?.split(",", 1)[0]?.trim() : null;
  const address = forwarded && isIP(forwarded) ? forwarded : "untrusted-or-direct";
  return {
    digest: createHash("sha256").update(address, "utf8").digest("hex"),
    trusted: Boolean(trusted && forwarded && isIP(forwarded))
  };
}

function sellerFingerprint(pathname: string) {
  const segments = pathname.split("/").filter(Boolean);
  const encodedSlug = segments[0] === "s"
    ? segments[1]
    : segments[0] === "api" && segments[1] === "public" && segments[2] === "storefronts"
      ? segments[3]
      : undefined;
  let slug: string | undefined;
  try {
    slug = encodedSlug === undefined ? undefined : decodeURIComponent(encodedSlug);
  } catch {
    slug = undefined;
  }
  const scope = isPublicSlug(slug) ? slug : "invalid-storefront";
  return createHash("sha256").update(scope, "utf8").digest("hex");
}

function isRedisUrl(value: string) {
  try {
    const parsed = new URL(value);
    return (parsed.protocol === "redis:" || parsed.protocol === "rediss:") && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}
