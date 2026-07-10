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
