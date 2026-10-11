import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  assertPublicStorefrontRateLimit,
  closePublicStorefrontRateLimiterForTest,
  PublicStorefrontRateLimitError
} from "../lib/public-storefront-rate-limit.server";
import { startIsolatedRedis, type IsolatedRedis } from "./helpers/isolated-redis";

describe("public storefront rate limit", () => {
  let redis: IsolatedRedis;

  beforeEach(async () => {
    redis = await startIsolatedRedis();
    process.env.APPSELL_REDIS_URL = redis.url;
    process.env.APPSELL_REDIS_PREFIX = `appsell:test:storefront-${process.pid}`;
    process.env.APPSELL_TRUST_PROXY_HEADERS = "true";
  });

  afterEach(async () => {
    await closePublicStorefrontRateLimiterForTest();
    await redis.stop();
    delete process.env.APPSELL_TRUST_PROXY_HEADERS;
  });

  it("limits a client atomically before the domain envelope is created", async () => {
    const headers = new Headers({ "x-forwarded-for": "203.0.113.10" });
    await Promise.all(Array.from({ length: 60 }, () =>
      assertPublicStorefrontRateLimit(headers, "/s/seller-one")));
    await expect(assertPublicStorefrontRateLimit(headers, "/s/seller-one")).rejects.toMatchObject({
      code: "rate_limited"
    });
    await expect(assertPublicStorefrontRateLimit(
      new Headers({ "x-forwarded-for": "203.0.113.11" }),
      "/s/seller-one"
    )).resolves.toBeUndefined();
  });

  it("allows the launch burst of 100 sellers and 1000 buyers with four navigations each", async () => {
    const requests = Array.from({ length: 1_000 }, (_, buyer) => {
      const seller = Math.floor(buyer / 10);
      const headers = new Headers({ "x-forwarded-for": buyerIp(buyer) });
      const sellerSlug = `seller-${String(seller).padStart(3, "0")}`;
      return Array.from({ length: 4 }, (_, request) => () => assertPublicStorefrontRateLimit(
        headers,
        request % 2 === 0
          ? `/s/${sellerSlug}/product-${request}`
          : `/api/public/storefronts/${sellerSlug}/products/product-${request}`
      ));
    }).flat();

    await runInBatches(requests);
  }, 30_000);

  it("caps a hot seller without denying another seller", async () => {
    await runInBatches(Array.from({ length: 300 }, (_, index) => () =>
      assertPublicStorefrontRateLimit(
        new Headers({ "x-forwarded-for": uniqueIp(index) }),
        "/s/hot-seller"
      )));
    await expect(assertPublicStorefrontRateLimit(
      new Headers({ "x-forwarded-for": uniqueIp(301) }),
      "/s/hot-seller"
    )).rejects.toMatchObject({ code: "rate_limited" });
    await expect(assertPublicStorefrontRateLimit(
      new Headers({ "x-forwarded-for": uniqueIp(302) }),
      "/s/independent-seller"
    )).resolves.toBeUndefined();
  });

  it("shares one seller budget across canonical, encoded and API paths", async () => {
    await runInBatches(Array.from({ length: 299 }, (_, index) => () =>
      assertPublicStorefrontRateLimit(
        new Headers({ "x-forwarded-for": uniqueIp(index) }),
        "/s/autonomia-demo"
      )));
    await expect(assertPublicStorefrontRateLimit(
      new Headers({ "x-forwarded-for": uniqueIp(299) }),
      "/s/%61utonomia-demo/product-one"
    )).resolves.toBeUndefined();
    await expect(assertPublicStorefrontRateLimit(
      new Headers({ "x-forwarded-for": uniqueIp(300) }),
      "/api/public/storefronts/autonomia-demo/products/product-two"
    )).rejects.toMatchObject({ code: "rate_limited" });
  });

  it("keeps the 6000 rpm global protection above all seller budgets", async () => {
    await runInBatches(Array.from({ length: 6_000 }, (_, index) => () =>
      assertPublicStorefrontRateLimit(
        new Headers({ "x-forwarded-for": uniqueIp(index) }),
        `/s/seller-${String(index % 100).padStart(3, "0")}`
      )));
    await expect(assertPublicStorefrontRateLimit(
      new Headers({ "x-forwarded-for": uniqueIp(6_001) }),
      "/s/new-seller"
    )).rejects.toMatchObject({ code: "rate_limited" });
  }, 30_000);

  it("returns unavailable during an outage and recovers after Redis restarts", async () => {
    const headers = new Headers({ "x-forwarded-for": "203.0.113.40" });
    await expect(assertPublicStorefrontRateLimit(headers, "/s/recovery-seller"))
      .resolves.toBeUndefined();

    await redis.stopServer();
    const startedAt = Date.now();
    await expect(assertPublicStorefrontRateLimit(headers, "/s/recovery-seller"))
      .rejects.toEqual(new PublicStorefrontRateLimitError("rate_limit_unavailable"));
    expect(Date.now() - startedAt).toBeLessThan(2_500);

    await redis.restartServer();
    await expect(assertPublicStorefrontRateLimit(headers, "/s/recovery-seller"))
      .resolves.toBeUndefined();
  }, 15_000);

  it("fails closed when Redis is misconfigured", async () => {
    await closePublicStorefrontRateLimiterForTest();
    process.env.APPSELL_REDIS_URL = "http://not-redis.invalid";
    await expect(assertPublicStorefrontRateLimit(new Headers(), "/s/seller-one")).rejects.toEqual(
      new PublicStorefrontRateLimitError("rate_limit_unavailable")
    );
  });

  it("does not let spoofed proxy headers bypass the seller budget", async () => {
    process.env.APPSELL_TRUST_PROXY_HEADERS = "false";
    await runInBatches(Array.from({ length: 300 }, (_, index) => () =>
      assertPublicStorefrontRateLimit(
        new Headers({ "x-forwarded-for": uniqueIp(index) }),
        "/s/untrusted-seller"
      )));
    await expect(assertPublicStorefrontRateLimit(new Headers({
      "x-forwarded-for": "198.51.100.200"
    }), "/s/untrusted-seller")).rejects.toMatchObject({ code: "rate_limited" });
    await expect(assertPublicStorefrontRateLimit(new Headers({
      "x-forwarded-for": "198.51.100.201"
    }), "/s/other-untrusted-seller")).resolves.toBeUndefined();
  });
});

function buyerIp(index: number) {
  return `198.18.${Math.floor(index / 250)}.${index % 250 + 1}`;
}

function uniqueIp(index: number) {
  return `2001:db8:${index.toString(16)}::1`;
}

async function runInBatches(requests: Array<() => Promise<void>>, batchSize = 200) {
  for (let offset = 0; offset < requests.length; offset += batchSize) {
    await Promise.all(requests.slice(offset, offset + batchSize).map((request) => request()));
  }
}
