import { expect, test } from "@playwright/test";

test("fails closed when the real Identity backend is not configured", async ({ request }) => {
  const mock = await request.get(
    "/dev/identity/authorize?state=x&nonce=x&code_challenge=x&code_challenge_method=S256"
  );
  expect(mock.status()).toBe(404);
  expect(mock.headers()["cache-control"]).toBe("no-store");

  const login = await request.get("/auth/login");
  expect(login.status()).toBe(503);
  expect(login.headers()["cache-control"]).toBe("no-store");
  expect(await login.json()).toEqual({ error: "identity_not_configured" });

  const callback = await request.get("/auth/callback?code=x&state=x");
  expect(callback.status()).toBe(503);
  expect(callback.headers()["cache-control"]).toBe("no-store");
  expect(callback.headers()["set-cookie"]).toContain("__Host-appsell_oauth_tx=");
  expect(callback.headers()["set-cookie"]).toContain("Secure");
  expect(callback.headers()["set-cookie"]).toContain("HttpOnly");
  expect(callback.headers()["set-cookie"]).not.toContain("Domain=");

  const session = await request.get("/api/session");
  expect(session.status()).toBe(401);
  expect(session.headers()["cache-control"]).toBe("no-store");
  expect(await session.json()).toEqual({ authenticated: false });
});
