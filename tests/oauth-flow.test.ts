import { describe, expect, it } from "vitest";
import { DevOAuthService, isSameOrigin } from "../lib/oauth-service";
import {
  productPublishCsrfCookieName,
  productUpdateCsrfCookieName,
  sessionCookieName,
  sessionCookieOptions,
  transactionCookieName,
  transactionCookieOptions
} from "../lib/cookies";

function complete(service: DevOAuthService) {
  const login = service.beginLogin();
  const code = service.authorize({ state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge });
  const sessionId = service.callback({ transactionId: login.transactionId, code, state: login.state });
  return { login, code, sessionId };
}

describe("OAuth BFF development spike", () => {
  it("completes state, nonce and PKCE and stores only the session hash", () => {
    const service = new DevOAuthService(() => 1_000);
    const { sessionId } = complete(service);
    expect(service.getSession(sessionId)).toMatchObject({ subject: "dev-user-001" });
    expect(service.assertNoRawSessionKey(sessionId)).toBe(true);
  });

  it("binds a fixed demo subject to the resulting session", () => {
    const service = new DevOAuthService(() => 1_000);
    const login = service.beginLogin();
    const code = service.authorize(
      { state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge },
      "local",
      "demo-user-v1"
    );
    const sessionId = service.callback({ transactionId: login.transactionId, code, state: login.state });
    expect(service.getSession(sessionId)).toMatchObject({ subject: "demo-user-v1" });
  });

  it("rejects a wrong state without consuming a legitimate transaction", () => {
    const service = new DevOAuthService();
    const login = service.beginLogin();
    const code = service.authorize({ state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge });
    expect(() => service.callback({ transactionId: login.transactionId, code, state: "wrong" })).toThrow("Invalid OAuth state");
    expect(service.callback({ transactionId: login.transactionId, code, state: login.state })).toBeTypeOf("string");
  });

  it("does not consume a valid transaction when an unknown code is supplied", () => {
    const service = new DevOAuthService();
    const login = service.beginLogin();
    const code = service.authorize({ state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge });
    expect(() => service.callback({ transactionId: login.transactionId, code: "unknown", state: login.state })).toThrow(
      "Authorization code expired or replayed"
    );
    expect(service.callback({ transactionId: login.transactionId, code, state: login.state })).toBeTypeOf("string");
  });

  it("rejects invalid PKCE and authorization-code replay", () => {
    const service = new DevOAuthService();
    const login = service.beginLogin();
    const code = service.authorize({ state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge });
    service.tamperCodeChallengeForTest(code);
    expect(() => service.callback({ transactionId: login.transactionId, code, state: login.state })).toThrow("PKCE validation failed");

    const second = complete(service);
    const replayTransaction = service.beginLogin();
    expect(() => service.callback({
      transactionId: replayTransaction.transactionId,
      code: second.code,
      state: replayTransaction.state
    })).toThrow("Authorization code expired or replayed");
  });

  it("rejects an altered or revoked opaque session", () => {
    const service = new DevOAuthService();
    const { sessionId } = complete(service);
    expect(service.getSession(`${sessionId}x`)).toBeNull();
    service.logout(sessionId);
    expect(service.getSession(sessionId)).toBeNull();
  });

  it("expires transactions, codes and sessions", () => {
    let time = 0;
    const service = new DevOAuthService(() => time, 10, 10, 10);
    const login = service.beginLogin();
    const code = service.authorize({ state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge });
    time = 11;
    expect(() => service.callback({ transactionId: login.transactionId, code, state: login.state })).toThrow("Login transaction expired");

    time = 20;
    const completed = complete(service);
    time = 31;
    expect(service.getSession(completed.sessionId)).toBeNull();
  });

  it("bounds active records, cleans expired entries and rate-limits each client", () => {
    let time = 0;
    const bounded = new DevOAuthService(() => time, 10, 10, 10, { maxRecords: 1 });
    bounded.beginLogin("one");
    expect(() => bounded.beginLogin("two")).toThrow("Too many active login transactions");
    time = 11;
    bounded.beginLogin("two");
    expect(bounded.recordCountsForTest().transactions).toBe(1);

    const limited = new DevOAuthService(() => 0, 10, 10, 10, {
      rateWindowMs: 100,
      maxPerClient: 1,
      maxGlobal: 10
    });
    limited.beginLogin("same-client");
    expect(() => limited.beginLogin("same-client")).toThrow("Too many OAuth requests");
    expect(() => limited.beginLogin("other-client")).not.toThrow();
  });

  it("uses host-only secure production cookie and a local development cookie", () => {
    expect(sessionCookieName(false)).toBe("appsell_session");
    expect(sessionCookieOptions(false)).toEqual({ httpOnly: true, secure: false, sameSite: "lax", path: "/" });
    expect(sessionCookieName(true)).toBe("__Host-appsell_session");
    expect(sessionCookieOptions(true)).toEqual({ httpOnly: true, secure: true, sameSite: "lax", path: "/" });
    expect(sessionCookieOptions(true)).not.toHaveProperty("domain");
    expect(transactionCookieName(false)).toBe("appsell_oauth_tx");
    expect(transactionCookieName(true)).toBe("__Host-appsell_oauth_tx");
    expect(transactionCookieOptions(true)).toEqual({
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      path: "/",
      maxAge: 300
    });
    expect(transactionCookieOptions(true)).not.toHaveProperty("domain");
    expect(productUpdateCsrfCookieName(false)).toBe("appsell_product_update_csrf");
    expect(productUpdateCsrfCookieName(true)).toBe("__Host-appsell_product_update_csrf");
    expect(productPublishCsrfCookieName(false)).toBe("appsell_product_publish_csrf");
    expect(productPublishCsrfCookieName(true)).toBe("__Host-appsell_product_publish_csrf");
  });

  it("accepts logout only from the exact origin", () => {
    expect(isSameOrigin(new Request("http://localhost:3000/auth/logout", { headers: { origin: "http://localhost:3000" } }))).toBe(true);
    expect(isSameOrigin(new Request("http://localhost:3000/auth/logout", { headers: { origin: "http://evil.local" } }))).toBe(false);
    expect(isSameOrigin(new Request("http://localhost:3000/auth/logout"))).toBe(false);
  });
});
