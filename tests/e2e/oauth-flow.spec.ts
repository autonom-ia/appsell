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
    bodyReads.push((async () => {
      const contentType = response.headers()["content-type"] ?? "";
      if (!/(text|json|javascript)/i.test(contentType)) return;
      try {
        responseBodies.push(await response.text());
      } catch {
        // Redirects and aborted dev assets do not always expose a body.
      }
    })());
  });

  await page.goto("/");
  await page.getByRole("link", { name: "Entrar com Identity local" }).click();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByRole("heading", { name: "Dashboard protegido" })).toBeVisible();

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
  expect(exposure.documentCookie).toBe("");
  expect(exposure.localStorage).toEqual([]);
  expect(exposure.sessionStorage).toEqual([]);
  expect(exposure.debugChannels).toEqual([]);
  for (const surface of [exposure.dom, exposure.url, ...exposure.localStorage, ...exposure.sessionStorage]) {
    expect(containsRawOrBase64Encoded(surface, rawSession)).toBe(false);
  }

  await page.reload();
  await expect(page.getByRole("heading", { name: "Dashboard protegido" })).toBeVisible();
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

test("rejects foreign logout and rejects replay after same-origin logout", async ({ context, page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar com Identity local" }).click();
  await expect(page).toHaveURL("/dashboard");
  const rawSession = (await context.cookies()).find((cookie) => cookie.name === "appsell_session")?.value ?? "";

  const foreign = await page.request.post("/auth/logout", {
    headers: { origin: "http://evil.local" },
    maxRedirects: 0
  });
  expect(foreign.status()).toBe(403);
  expect(foreign.headers()["cache-control"]).toBe("no-store");
  expect((await page.request.get("/api/session")).status()).toBe(200);

  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page).toHaveURL("/");
  expect((await context.cookies()).some((cookie) => cookie.name === "appsell_session")).toBe(false);
  expect((await page.request.get("/api/session")).status()).toBe(401);
  const replay = await page.request.get("/api/session", { headers: { cookie: `appsell_session=${rawSession}` } });
  expect(replay.status()).toBe(401);
});
