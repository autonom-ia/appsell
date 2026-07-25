import { readFile } from "node:fs/promises";

import { describe, expect, test, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  describeCallbackFailure,
  reportOAuthCallbackFailure
} from "../lib/oauth-callback-telemetry";
import {
  IdentityOAuthAmbiguousError,
  IdentityOAuthConfigurationError,
  IdentityOAuthProtocolError
} from "../lib/identity-oauth-client.server";

describe("oauth callback telemetry", () => {
  test("records the outcome and the reason on a single structured line", () => {
    const lines: string[] = [];
    reportOAuthCallbackFailure(
      { outcome: "invalid_oauth_callback", reason: "Identity token verification failed (claim iss)." },
      (line) => lines.push(line)
    );
    expect(lines).toHaveLength(1);
    expect(JSON.parse(lines[0] as string)).toEqual({
      event: "oauth_callback_failed",
      outcome: "invalid_oauth_callback",
      reason: "Identity token verification failed (claim iss)."
    });
  });

  test.each([
    [new IdentityOAuthProtocolError("Identity token verification failed (claim aud)."),
      "Identity token verification failed (claim aud)."],
    [new IdentityOAuthConfigurationError("APPSELL_IDENTITY_NONCE_MODE must be required, disabled or broker."),
      "APPSELL_IDENTITY_NONCE_MODE must be required, disabled or broker."],
    [new IdentityOAuthAmbiguousError("Identity token exchange outcome is ambiguous."),
      "Identity token exchange outcome is ambiguous."]
  ])("keeps the fixed message of adapter errors", (error, expected) => {
    expect(describeCallbackFailure(error)).toBe(expected);
  });

  test("reduces an unknown error to its class so an arbitrary message cannot leak", () => {
    const error = new Error("boom: eyJhbGciOi.SECRET.payload for user@example.com");
    expect(describeCallbackFailure(error)).toBe("unclassified error");
    expect(describeCallbackFailure(error)).not.toContain("SECRET");
  });

  test("handles a non-Error throw without inspecting it", () => {
    expect(describeCallbackFailure({ token: "eyJhbGciOi.SECRET" })).toBe("non-error thrown");
  });

  test("does not trust a forged error that merely claims an adapter name", () => {
    const forged = new Error("attacker-controlled: eyJhbGciOi.SECRET");
    forged.name = "IdentityOAuthProtocolError";
    expect(describeCallbackFailure(forged)).toBe("unclassified error");
    expect(describeCallbackFailure(forged)).not.toContain("SECRET");
  });
});

// Behavioural coverage of the integration point: actually drives the route's
// catch block and asserts what reaches the log and what reaches the caller.
describe("callback route failure reporting", () => {
  const ORIGIN = "https://sell.test";

  async function callRoute(query: string, cookie?: string) {
    vi.resetModules();
    // Deliberately unset identity configuration so the handler throws inside
    // the try block, which is the path under test.
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("APPSELL_OAUTH_MODE", "identity");
    vi.stubEnv("APPSELL_PUBLIC_ORIGIN", ORIGIN);
    const { GET } = await import("../app/auth/callback/route");
    const { NextRequest } = await import("next/server");
    const request = new NextRequest(`${ORIGIN}/auth/callback${query}`, {
      headers: cookie ? { cookie } : {}
    });
    const errors: unknown[][] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args) => {
      errors.push(args);
    });
    try {
      const response = await GET(request);
      return { response, errors };
    } finally {
      spy.mockRestore();
      vi.unstubAllEnvs();
    }
  }

  test("logs the reason for a real provider return and keeps it out of the response body", async () => {
    const { response, errors } = await callRoute("?code=abc&state=xyz");
    const body = await response.json();
    expect(Object.keys(body)).toEqual(["error"]);
    expect(JSON.stringify(body)).not.toContain("reason");
    expect(errors).toHaveLength(1);
    const line = JSON.parse(String(errors[0]?.[0]));
    expect(line.event).toBe("oauth_callback_failed");
    expect(line.outcome).toBe(body.error);
    expect(line.reason).toBeTruthy();
  });

  test("still logs when the transaction cookie is missing but the provider did return", async () => {
    const { errors } = await callRoute("?code=abc&state=xyz");
    expect(errors).toHaveLength(1);
  });

  test("logs a provider-side error return such as access_denied", async () => {
    const { errors } = await callRoute("?error=access_denied&state=xyz");
    expect(errors).toHaveLength(1);
  });

  test("stays silent on a bare hit with no provider parameters", async () => {
    const { errors } = await callRoute("");
    expect(errors).toHaveLength(0);
  });
});

// Guards the integration point itself: without this, deleting the call from the
// route would leave every unit test above green while production went back to
// discarding the reason.
describe("callback route wiring", () => {
  test("the callback route reports the failure reason and gates it on a complete callback", async () => {
    const source = await readFile(
      new URL("../app/auth/callback/route.ts", import.meta.url),
      "utf8"
    );
    expect(source).toContain("reportOAuthCallbackFailure(");
    expect(source).toContain("describeCallbackFailure(error)");
    expect(source).toMatch(/if \(looksLikeProviderReturn\) \{\s*reportOAuthCallbackFailure/);
    // The public body must stay the coarse outcome code, never the reason.
    expect(source).toContain("NextResponse.json({ error: outcome }");
    expect(source).not.toMatch(/NextResponse\.json\(\{[^}]*reason/);
  });
});
