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
    expect(describeCallbackFailure(error)).toBe("Error");
    expect(describeCallbackFailure(error)).not.toContain("SECRET");
  });

  test("handles a non-Error throw without inspecting it", () => {
    expect(describeCallbackFailure({ token: "eyJhbGciOi.SECRET" })).toBe("non-error thrown");
  });

  test("does not trust a forged error that merely claims an adapter name", () => {
    const forged = new Error("attacker-controlled: eyJhbGciOi.SECRET");
    forged.name = "IdentityOAuthProtocolError";
    expect(describeCallbackFailure(forged)).toBe("Error");
    expect(describeCallbackFailure(forged)).not.toContain("SECRET");
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
    expect(source).toMatch(/if \(isCompleteCallback\) \{\s*reportOAuthCallbackFailure/);
    // The public body must stay the coarse outcome code, never the reason.
    expect(source).toContain("NextResponse.json({ error: outcome }");
    expect(source).not.toMatch(/NextResponse\.json\(\{[^}]*reason/);
  });
});
