import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("sensitive route logging", () => {
  it.each([
    "/auth/login",
    "/auth/callback?code=secret&state=secret",
    "/dev/identity/authorize?nonce=secret&code_challenge=secret"
  ])("suppresses the full request target for %s", (requestTarget) => {
    if (!nextConfig.logging) throw new Error("Next.js request logging configuration is missing.");
    const incoming = nextConfig.logging.incomingRequests;
    if (typeof incoming !== "object") throw new Error("Next.js incoming request ignore rules are missing.");
    const ignored = incoming.ignore ?? [];
    expect(ignored.some((pattern) => pattern.test(requestTarget))).toBe(true);
  });
});

describe("browser security headers", () => {
  it("prevents signed storage URLs and internal paths from leaking as referrers", async () => {
    if (!nextConfig.headers) throw new Error("Next.js security headers are missing.");
    const rules = await nextConfig.headers();
    const global = rules.find((rule) => rule.source === "/:path*");
    expect(global?.headers).toEqual(expect.arrayContaining([
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" }
    ]));
  });

  it.each([
    "/api/storefronts/:sellerSlug/products/:productSlug/cover",
    "/api/storefronts/:sellerSlug/products/:productSlug/banner",
    "/api/storefronts/:sellerSlug/brand/:kind"
  ])("allows the public store image %s to render on another domain", async (source) => {
    if (!nextConfig.headers) throw new Error("Next.js security headers are missing.");
    const rules = await nextConfig.headers();
    const rule = rules.find((candidate) => candidate.source === source);
    expect(rule?.headers).toContainEqual({
      key: "Cross-Origin-Resource-Policy",
      value: "cross-origin"
    });
  });
});
