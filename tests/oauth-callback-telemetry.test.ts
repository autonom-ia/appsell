import { readFile } from "node:fs/promises";

import { describe, expect, test, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  describeCallbackFailure,
  reportOAuthCallbackFailure
} from "../lib/oauth-callback-telemetry";
import { shouldRestartLogin } from "../lib/oauth-callback-recovery";
import { OAuthFlowError } from "../lib/oauth-service";
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

  test("still logs when only the state survives, as with an expired transaction", async () => {
    const { errors } = await callRoute("?state=xyz");
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

// A sign-up takes longer than the login transaction TTL (creating the account,
// waiting for the confirmation e-mail, confirming). The user did everything
// right, so the callback must restart the login instead of answering with raw
// JSON.
describe("recoverable callback restarts the login", () => {
  test("restarts when the transaction expired and no restart happened yet", () => {
    const expired = new OAuthFlowError("Login transaction expired.", "recoverable");
    expect(shouldRestartLogin(expired, false)).toBe(true);
  });

  test("restarts when the provider returned but the transaction cookie is gone", () => {
    const missing = new OAuthFlowError("Missing callback parameters.", "recoverable");
    expect(shouldRestartLogin(missing, false)).toBe(true);
  });

  test("does not loop: the guard blocks a second restart", () => {
    const expired = new OAuthFlowError("Login transaction expired.", "recoverable");
    expect(shouldRestartLogin(expired, true)).toBe(false);
  });

  test.each([
    ["invalid state", new OAuthFlowError("Invalid OAuth state.")],
    ["replayed code", new OAuthFlowError("Authorization code expired or replayed.")],
    ["token verification", new IdentityOAuthProtocolError("Identity token verification failed (claim iss).")],
    ["unknown error", new Error("boom")]
  ])("never restarts on a final failure: %s", (_label, error) => {
    expect(shouldRestartLogin(error, false)).toBe(false);
  });

  test("defaults to final, so a new throw site must opt in deliberately", () => {
    expect(new OAuthFlowError("anything").failure).toBe("final");
  });
});

// Guards the wiring: the helper above is only useful if the route calls it and
// answers with a redirect that also arms the guard and clears the dead
// transaction cookie.
describe("callback route restart wiring", () => {
  test("the route restarts the login through the helper and arms the guard", async () => {
    const source = await readFile(
      new URL("../app/auth/callback/route.ts", import.meta.url),
      "utf8"
    );
    expect(source).toContain("shouldRestartLogin(error, alreadyRecovered)");
    expect(source).toMatch(/NextResponse\.redirect\(new URL\("\/auth\/login"/);
    expect(source).toContain("retry.cookies.set(recoveryCookieName(), \"1\", recoveryCookieOptions())");
    expect(source).toContain("expireTransactionCookie(retry)");
    // The guard must be cleared on success, otherwise a later expiry within the
    // guard window would fall through to the raw error body.
    expect(source).toMatch(/recoveryCookieName\(\), "", \{ \.\.\.recoveryCookieOptions\(\), maxAge: 0 \}/);
  });
});

// Guards the integration point itself: without this, deleting the call from the
// route would leave every unit test above green while production went back to
// discarding the reason.
describe("callback route wiring", () => {
  test("the callback route reports the failure reason and gates it on a provider return", async () => {
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
