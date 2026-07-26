import { describe, expect, test, vi } from "vitest";

vi.mock("server-only", () => ({}));

// Drives the real route handler over HTTP semantics. The unit tests for
// `shouldRestartLogin` prove the decision; these prove the response the browser
// actually receives — status, Location, Set-Cookie flags and body — which a
// source-reading test cannot.
//
// The OAuth service is mocked so the handler is exercised without Redis: the
// subject here is the route's error handling, not the store.

const ORIGIN = "https://sell.test";

type Scenario = "recoverable" | "final";

function mockService(scenario: Scenario) {
  vi.doMock("@/lib/oauth-service.server", async () => {
    const { OAuthFlowError } = await import("../lib/oauth-service");
    return {
      oauthRuntimeMode: () => "identity",
      isOAuthRuntimeConfigurationError: () => false,
      identityOAuthClient: () => ({ exchangeAuthorizationCode: () => undefined }),
      oauthService: () => ({
        callbackIdentity: () => {
          throw scenario === "recoverable"
            ? new OAuthFlowError("Login transaction expired.", "recoverable")
            : new OAuthFlowError("Invalid OAuth state.");
        },
        callback: () => {
          throw new OAuthFlowError("Invalid OAuth state.");
        }
      })
    };
  });
}

async function callRoute(
  query: string,
  options: { cookie?: string; accept?: string; scenario?: Scenario } = {}
) {
  vi.resetModules();
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("APPSELL_PUBLIC_ORIGIN", ORIGIN);
  mockService(options.scenario ?? "recoverable");
  const { GET } = await import("../app/auth/callback/route");
  const { NextRequest } = await import("next/server");
  const headers: Record<string, string> = {};
  if (options.cookie) headers.cookie = options.cookie;
  if (options.accept) headers.accept = options.accept;
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    return await GET(new NextRequest(`${ORIGIN}/auth/callback${query}`, { headers }));
  } finally {
    spy.mockRestore();
    vi.unstubAllEnvs();
    vi.doUnmock("@/lib/oauth-service.server");
  }
}

describe("callback restarts the login after a sign-up outlives the transaction", () => {
  test("redirects to the login instead of answering with an error", async () => {
    const response = await callRoute("?code=abc&state=xyz");
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(`${ORIGIN}/auth/login`);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  test("arms the one-shot guard with the hardened cookie flags", async () => {
    const response = await callRoute("?code=abc&state=xyz");
    const guard = response.headers.getSetCookie()
      .find((cookie) => cookie.startsWith("__Host-appsell_oauth_recovery="));
    expect(guard).toBeDefined();
    expect(guard).toContain("__Host-appsell_oauth_recovery=1");
    expect(guard).toContain("HttpOnly");
    expect(guard).toContain("Secure");
    expect(guard?.toLowerCase()).toContain("samesite=lax");
    expect(guard).toContain("Path=/");
    expect(guard).toContain("Max-Age=120");
  });

  test("clears the dead transaction cookie on the way out", async () => {
    const response = await callRoute("?code=abc&state=xyz");
    const transaction = response.headers.getSetCookie()
      .find((cookie) => cookie.startsWith("__Host-appsell_oauth_tx="));
    expect(transaction).toContain("Max-Age=0");
  });

  test("does not loop: with the guard already set it stops redirecting", async () => {
    const response = await callRoute("?code=abc&state=xyz", {
      cookie: "__Host-appsell_oauth_recovery=1"
    });
    expect(response.status).toBe(400);
    expect(response.headers.get("location")).toBeNull();
  });

  test("a final failure never redirects", async () => {
    const response = await callRoute("?code=abc&state=xyz", { scenario: "final" });
    expect(response.status).toBe(400);
    expect(response.headers.get("location")).toBeNull();
  });
});

describe("final failures answer a person, not a JSON blob", () => {
  test("a browser navigation gets a readable page with a way back", async () => {
    const response = await callRoute("?code=abc&state=xyz", {
      cookie: "__Host-appsell_oauth_recovery=1",
      accept: "text/html,application/xhtml+xml"
    });
    expect(response.headers.get("content-type")).toContain("text/html");
    const body = await response.text();
    expect(body).toContain("Não foi possível concluir o acesso");
    expect(body).toContain('href="/auth/login"');
    expect(body).not.toContain('{"error"');
  });

  test("an API client still gets the machine-readable body", async () => {
    const response = await callRoute("?code=abc&state=xyz", {
      cookie: "__Host-appsell_oauth_recovery=1",
      accept: "application/json"
    });
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(await response.json()).toEqual({ error: "invalid_oauth_callback" });
  });

  test("the visible page is identical for different outcomes", async () => {
    const invalid = await callRoute("?code=abc&state=xyz", {
      cookie: "__Host-appsell_oauth_recovery=1",
      accept: "text/html"
    });
    const denied = await callRoute("?error=access_denied&state=xyz", {
      cookie: "__Host-appsell_oauth_recovery=1",
      accept: "text/html"
    });
    const stripComment = (html: string) => html.replace(/<!--[\s\S]*?-->/g, "");
    expect(stripComment(await invalid.text())).toBe(stripComment(await denied.text()));
  });
});
