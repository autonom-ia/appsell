import { expect, test } from "@playwright/test";

function containsRawOrBase64Encoded(input: string, raw: string) {
  if (input.includes(raw)) return true;
  const candidates = input.match(/[A-Za-z0-9+/_=-]{40,}/g) ?? [];
  return candidates.some((candidate) => {
    try {
      return Buffer.from(candidate.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8").includes(raw);
    } catch {
      return false;
    }
  });
}

test("keeps the opaque session out of browser-readable surfaces", async ({ context, page }) => {
  const responseBodies: string[] = [];
  const bodyReads: Promise<void>[] = [];
  page.on("response", (response) => {
    const read = (async () => {
      const contentType = response.headers()["content-type"] ?? "";
      if (!/(text|json|javascript)/i.test(contentType)) return;
      if (/text\/event-stream/i.test(contentType)) return;
      // The final document is inspected below through documentElement.innerHTML,
      // including inline RSC/Flight payloads. Re-reading it can hang in Chromium dev mode.
      if (response.request().resourceType() === "document") return;
      try {
        responseBodies.push(await response.text());
      } catch {
        // Redirects and aborted dev assets do not always expose a body.
      }
    })();
    bodyReads.push(Promise.race([
      read,
      new Promise<void>((_, reject) => setTimeout(() => reject(new Error(`Timed out inspecting response body: ${new URL(response.url()).pathname}`)), 5_000)),
    ]));
  });

  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByRole("heading", { name: "Bom dia, creator." })).toBeVisible();

  const sessionCookie = (await context.cookies()).find((cookie) => cookie.name === "appsell_session");
  expect(sessionCookie).toMatchObject({ httpOnly: true, sameSite: "Lax" });
  const rawSession = sessionCookie?.value ?? "";
  expect(rawSession).not.toBe("");

  const exposure = await page.evaluate(() => {
    const storageValues = (storage: Storage) => Array.from({ length: storage.length }, (_, index) => {
      const key = storage.key(index);
      return key ? storage.getItem(key) : null;
    }).filter((value): value is string => value !== null);
    return {
      documentCookie: document.cookie,
      dom: document.documentElement.innerHTML,
      localStorage: storageValues(localStorage),
      sessionStorage: storageValues(sessionStorage),
      debugChannels: Object.keys(sessionStorage).filter((key) => key.startsWith("__next_debug_channel")),
      url: location.href
    };
  });
  const readableCookies = Object.fromEntries(exposure.documentCookie.split("; ").map((cookie) => {
    const separator = cookie.indexOf("=");
    return [cookie.slice(0, separator), cookie.slice(separator + 1)];
  }));
  expect(Object.keys(readableCookies).sort()).toEqual([
    "appsell_csrf",
    "appsell_product_csrf",
    "appsell_product_publish_csrf",
    "appsell_product_update_csrf",
    "appsell_seller_csrf"
  ]);
  for (const value of Object.values(readableCookies)) {
    expect(value).toMatch(/^[A-Za-z0-9_-]+$/);
  }
  expect(exposure.documentCookie).not.toContain("appsell_session");
  expect(exposure.localStorage).toEqual([]);
  expect(exposure.sessionStorage).toEqual([]);
  expect(exposure.debugChannels).toEqual([]);
  const directIndex = exposure.dom.indexOf(rawSession);
  const redactedContext = directIndex < 0 ? "not present" : exposure.dom.slice(Math.max(0, directIndex - 100), directIndex + rawSession.length + 100).replace(rawSession, "[REDACTED]");
  expect(directIndex, `DOM directly exposed the opaque session near: ${redactedContext}`).toBe(-1);
  for (const [name, surface] of Object.entries({ dom: exposure.dom, url: exposure.url, localStorage: exposure.localStorage.join("\n"), sessionStorage: exposure.sessionStorage.join("\n") })) {
    expect(containsRawOrBase64Encoded(surface, rawSession), `${name} exposed the opaque session`).toBe(false);
  }

  await page.reload();
  await expect(page.getByRole("heading", { name: "Bom dia, creator." })).toBeVisible();
  await Promise.all(bodyReads);
  expect(responseBodies.some((body) => containsRawOrBase64Encoded(body, rawSession))).toBe(false);
  expect(page.url()).not.toMatch(/[?&](code|state)=/);
});

test("preserves a legitimate transaction after an invalid callback", async ({ context, page }) => {
  const login = await page.request.get("/auth/login", { maxRedirects: 0 });
  expect(login.status()).toBe(307);
  expect(login.headers()["cache-control"]).toBe("no-store");

  const transaction = (await context.cookies()).find((cookie) => cookie.name === "appsell_oauth_tx");
  expect(transaction).toMatchObject({ httpOnly: true, sameSite: "Lax" });

  const authorize = await page.request.get(login.headers().location, { maxRedirects: 0 });
  expect(authorize.status()).toBe(307);
  expect(authorize.headers()["cache-control"]).toBe("no-store");
  const callbackUrl = authorize.headers().location;
  const invalidUrl = callbackUrl.replace(/([?&]state=)[^&]+/, "$1wrong");

  const invalid = await page.request.get(invalidUrl, { maxRedirects: 0 });
  expect(invalid.status()).toBe(400);
  expect(invalid.headers()["cache-control"]).toBe("no-store");
  expect((await context.cookies()).some((cookie) => cookie.name === "appsell_oauth_tx")).toBe(true);

  const valid = await page.request.get(callbackUrl, { maxRedirects: 0 });
  expect(valid.status()).toBe(307);
  expect(valid.headers()["cache-control"]).toBe("no-store");
  expect(valid.headers().location).toBe("http://localhost:3000/dashboard");
  const finalCookies = await context.cookies();
  expect(finalCookies.some((cookie) => cookie.name === "appsell_oauth_tx")).toBe(false);
  expect(finalCookies.some((cookie) => cookie.name === "appsell_session")).toBe(true);
});

test("rejects a forged session before reading oversized mutation bodies", async ({ context, page }) => {
  await context.addCookies([{
    name: "appsell_session",
    value: "forged-session",
    url: "http://localhost:3000",
    httpOnly: true,
    sameSite: "Lax"
  }]);
  for (const endpoint of [
    "/api/sellers",
    "/api/sellers/52000000-0000-4000-8000-000000000001/products"
  ]) {
    const response = await page.request.post(endpoint, {
      headers: {
        origin: "http://localhost:3000",
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8"
      },
      data: `padding=${"x".repeat(20_000)}`
    });
    expect(response.status()).toBe(401);
    expect(await response.json()).toEqual({ error: "unauthorized" });
  }
});

test("rejects forged origin and CSRF before revoking a same-origin session", async ({ browser, context, page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
  const authCookies = await context.cookies();
  const rawSession = authCookies.find((cookie) => cookie.name === "appsell_session")?.value ?? "";
  const csrf = authCookies.find((cookie) => cookie.name === "appsell_csrf");
  expect(csrf).toMatchObject({ httpOnly: false, sameSite: "Strict" });

  const otherContext = await browser.newContext();
  const otherPage = await otherContext.newPage();
  await otherPage.goto("/");
  await otherPage.getByRole("link", { name: "Entrar no ambiente local" }).click();
  const otherCsrf = (await otherContext.cookies()).find((cookie) => cookie.name === "appsell_csrf")?.value ?? "";
  await otherContext.close();

  for (const origin of [undefined, "null", "http://evil.local", "http://admin.localhost:3000"]) {
    const headers: Record<string, string> = { "content-type": "application/x-www-form-urlencoded" };
    if (origin) headers.origin = origin;
    const rejected = await page.request.post("/auth/logout", {
      headers,
      data: new URLSearchParams({ _csrf: csrf?.value ?? "" }).toString(),
      maxRedirects: 0
    });
    expect(rejected.status()).toBe(403);
    expect(rejected.headers()["cache-control"]).toBe("no-store");
  }
  for (const token of ["", otherCsrf]) {
    const rejected = await page.request.post("/auth/logout", {
      headers: {
        origin: "http://localhost:3000",
        "content-type": "application/x-www-form-urlencoded"
      },
      data: new URLSearchParams({ _csrf: token }).toString(),
      maxRedirects: 0
    });
    expect(rejected.status()).toBe(403);
  }
  expect((await page.request.get("/api/session")).status()).toBe(200);

  await page.getByRole("button", { name: "Sair com segurança" }).first().click();
  await expect(page).toHaveURL("/");
  expect((await context.cookies()).some((cookie) => cookie.name === "appsell_session")).toBe(false);
  expect((await page.request.get("/api/session")).status()).toBe(401);
  const replay = await page.request.get("/api/session", { headers: { cookie: `appsell_session=${rawSession}` } });
  expect(replay.status()).toBe(401);
});
