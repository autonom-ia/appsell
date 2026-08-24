import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test } from "vitest";
import { chromium, expect as pwExpect, type Browser, type Page } from "@playwright/test";

import seedManifest from "../demo/content/codex-na-pratica/manifest.json";
import { deriveCsrfToken } from "../lib/csrf";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;

test.skipIf(!domainApiUrl)("serves isolated admin and buyer demos with protected PDFs", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const port = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const distDir = `.next-demo-${suffix}`;
  const origin = `http://localhost:${port}`;
  const csrfKey = randomBytes(32);
  const server = startBff(port, distDir, {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:demo-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: csrfKey.toString("base64"),
    APPSELL_PUBLIC_ORIGIN: origin,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "test-v1", key: randomBytes(32).toString("base64"), active: true }
    ]),
    APPSELL_DOMAIN_API_URL: requiredEnvironment("APPSELL_TEST_DOMAIN_API_URL"),
    APPSELL_INTERNAL_ISSUER: requiredEnvironment("APPSELL_INTERNAL_ISSUER"),
    APPSELL_INTERNAL_AUDIENCE: requiredEnvironment("APPSELL_INTERNAL_AUDIENCE"),
    APPSELL_IDENTITY_ISSUER: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: requiredEnvironment("APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"),
    APPSELL_SERVICE_CREDENTIAL_B64: requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64"),
    APP_ENV: "ci",
    APPSELL_DEMO_ENABLED: "true",
    NEXT_TELEMETRY_DISABLED: "1"
  });
  let browser: Browser | undefined;
  let redisStopped = false;

  try {
    await waitForBff(server, port);
    const invalid = await fetch(`${origin}/auth/login?persona=attacker`, { redirect: "manual" });
    expect(invalid.status).toBe(400);

    const sellerId = seedManifest.seller.id;
    const productId = seedManifest.product.id;
    const courseId = seedManifest.course.id;
    const moduleId = seedManifest.module.id;
    const firstLesson = seedManifest.lessons[0]!;
    const policyAdmin = await login(origin, "admin");
    const initialProduct = await readProductDetail(
      origin,
      policyAdmin.session,
      sellerId,
      productId
    );
    const assetsPath = `/api/sellers/${sellerId}/products/${productId}` +
      `/courses/${courseId}/modules/${moduleId}/lessons/${firstLesson.lesson_id}/assets`;
    const assetsResponse = await fetch(`${origin}${assetsPath}`, {
      headers: { cookie: policyAdmin.session }
    });
    expect(assetsResponse.status, await assetsResponse.clone().text()).toBe(200);
    const assetsPayload = await assetsResponse.json() as {
      assets: Array<{ id: string; content_role: string; download_allowed: boolean }>;
      csrf: { policy: string };
    };
    expect(assetsPayload.assets.find((asset) => asset.id === firstLesson.asset_id)).toMatchObject({
      content_role: "course_content",
      download_allowed: false
    });
    const policyResponse = await fetch(`${origin}${assetsPath}/${firstLesson.asset_id}/policy`, {
      method: "PATCH",
      headers: { cookie: policyAdmin.session, origin },
      body: formData({
        _csrf: assetsPayload.csrf.policy,
        idempotency_key: randomUUID(),
        expected_course_version: String(initialProduct.product.courses[0]!.draft_version),
        content_role: "support_material",
        download_allowed: "true"
      })
    });
    expect(policyResponse.status, await policyResponse.clone().text()).toBe(200);
    expect(await policyResponse.json()).toMatchObject({
      asset_id: firstLesson.asset_id,
      content_role: "support_material",
      download_allowed: true,
      course_version: initialProduct.product.courses[0]!.draft_version + 1
    });
    const publishable = await readProductDetail(
      origin,
      policyAdmin.session,
      sellerId,
      productId
    );
    expect(publishable.product.readiness.can_publish).toBe(true);
    const publicationResponse = await fetch(
      `${origin}/api/sellers/${sellerId}/products/${productId}/publications`,
      {
        method: "POST",
        headers: { cookie: policyAdmin.session, origin },
        body: formData({
          _csrf: publishable.csrf.publish,
          idempotency_key: randomUUID(),
          offer_id: publishable.product.offer.id,
          expected_product_version: String(publishable.product.draft_version),
          expected_offer_version: String(publishable.product.offer.draft_version),
          course_versions: JSON.stringify(publishable.product.courses.map((course) => ({
            id: course.id,
            version: course.draft_version
          })))
        })
      }
    );
    expect(publicationResponse.status, await publicationResponse.clone().text()).toBe(201);
    expect(await publicationResponse.json()).toMatchObject({
      product_id: productId,
      publication_version: initialProduct.product.publication.version + 1,
      created: true
    });

    const buyer = await login(origin, "buyer");
    expect(buyer.redirect).toBe(`${origin}/library`);
    for (const cookieName of [
      "appsell_seller_csrf",
      "appsell_product_csrf",
      "appsell_product_update_csrf",
      "appsell_product_publish_csrf"
    ]) {
      const expired = buyer.setCookies.find((cookie) => cookie.startsWith(`${cookieName}=`));
      expect(expired).toContain("Max-Age=0");
    }
    const sellers = await fetch(`${origin}/api/sellers`, { headers: { cookie: buyer.session } });
    expect(sellers.status).toBe(403);
    expect(await sellers.json()).toEqual({ error: "demo_buyer_capability_forbidden" });
    const products = await fetch(`${origin}/api/sellers/${sellerId}/products`, {
      headers: { cookie: buyer.session }
    });
    expect(products.status).toBe(403);
    expect(await products.json()).toEqual({ error: "demo_buyer_capability_forbidden" });
    const buyerSessionId = buyer.session.slice(buyer.session.indexOf("=") + 1);
    const forgedSellerCsrf = deriveCsrfToken(
      buyerSessionId,
      "POST:/api/sellers",
      csrfKey
    );
    const forbiddenOnboarding = await fetch(`${origin}/api/sellers`, {
      method: "POST",
      headers: { cookie: buyer.session, origin },
      body: new URLSearchParams({
        _csrf: forgedSellerCsrf,
        idempotency_key: randomUUID(),
        slug: `buyer-forbidden-${suffix}`,
        display_name: "Buyer Must Not Own"
      })
    });
    expect(forbiddenOnboarding.status).toBe(403);
    expect(await forbiddenOnboarding.json()).toEqual({ error: "demo_buyer_capability_forbidden" });
    const library = await fetch(`${origin}/api/library`, { headers: { cookie: buyer.session } });
    expect(library.status, await library.clone().text()).toBe(200);
    expect(await library.json()).toEqual({ items: [] });

    const checkoutQuote = await fetch(`${origin}/api/demo-checkout`, {
      headers: { cookie: buyer.session }
    });
    expect(checkoutQuote.status, await checkoutQuote.clone().text()).toBe(200);
    const checkoutPayload = await checkoutQuote.json() as {
      checkout: { already_owned: boolean; offer: { unit_amount_minor: number; currency: string } };
      coupon_hint: string;
      csrf: { complete: string };
    };
    expect(checkoutPayload).toMatchObject({
      checkout: { already_owned: false, offer: { unit_amount_minor: 9900, currency: "BRL" } },
      coupon_hint: "AUTONOMIA100"
    });
    const invalidOrigin = await fetch(`${origin}/api/demo-checkout`, {
      method: "POST",
      headers: { cookie: buyer.session, origin: "https://attacker.example" },
      body: formData({
        _csrf: checkoutPayload.csrf.complete,
        idempotency_key: randomUUID(),
        coupon_code: "AUTONOMIA100"
      })
    });
    expect(invalidOrigin.status).toBe(403);
    expect(await invalidOrigin.json()).toEqual({ error: "invalid_csrf" });

    const declaredTooLarge = await fetch(`${origin}/api/demo-checkout`, {
      method: "POST",
      headers: { cookie: buyer.session, origin },
      body: new URLSearchParams({
        _csrf: checkoutPayload.csrf.complete,
        idempotency_key: randomUUID(),
        coupon_code: "AUTONOMIA100",
        padding: "x".repeat(17_000)
      })
    });
    expect(declaredTooLarge.status).toBe(413);
    expect(await declaredTooLarge.json()).toEqual({ error: "payload_too_large" });

    const streamedPayload = new TextEncoder().encode(new URLSearchParams({
      _csrf: checkoutPayload.csrf.complete,
      idempotency_key: randomUUID(),
      coupon_code: "AUTONOMIA100",
      padding: "x".repeat(17_000)
    }).toString());
    const streamedBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(streamedPayload.subarray(0, 8_000));
        controller.enqueue(streamedPayload.subarray(8_000));
        controller.close();
      }
    });
    const streamedTooLarge = await fetch(`${origin}/api/demo-checkout`, {
      method: "POST",
      headers: {
        cookie: buyer.session,
        origin,
        "content-type": "application/x-www-form-urlencoded"
      },
      body: streamedBody,
      duplex: "half"
    } as RequestInit & { duplex: "half" });
    expect(streamedTooLarge.status).toBe(413);
    expect(await streamedTooLarge.json()).toEqual({ error: "payload_too_large" });

    const completedCheckout = await fetch(`${origin}/api/demo-checkout`, {
      method: "POST",
      headers: { cookie: buyer.session, origin },
      body: formData({
        _csrf: checkoutPayload.csrf.complete,
        idempotency_key: randomUUID(),
        coupon_code: "AUTONOMIA100"
      })
    });
    expect(completedCheckout.status, await completedCheckout.clone().text()).toBe(202);
    expect(await completedCheckout.json()).toMatchObject({
      checkout: { total_amount_minor: 0, provider_called: false }
    });
    await waitForCheckoutAccess(origin, buyer.session);

    const ownedLibrary = await fetch(`${origin}/api/library`, {
      headers: { cookie: buyer.session }
    });
    expect(ownedLibrary.status, await ownedLibrary.clone().text()).toBe(200);
    const libraryPayload = await ownedLibrary.json() as {
      items: Array<{
        entitlement_id: string;
        product: { title: string };
      }>;
    };
    expect(libraryPayload.items).toHaveLength(1);
    expect(libraryPayload.items[0]?.product.title).toBe("Codex na prática");
    expect(libraryPayload.items[0]).not.toHaveProperty("snapshot");
    expect(libraryPayload.items[0]).not.toHaveProperty("assets");

    const entitlementId = libraryPayload.items[0]!.entitlement_id;
    const player = await fetch(`${origin}/api/library/items/${entitlementId}`, {
      headers: { cookie: buyer.session }
    });
    expect(player.status, await player.clone().text()).toBe(200);
    const playerPayload = await player.json() as {
      player: {
        selected_asset: {
          view_href: string;
          download_href: string | null;
          title: string;
          content_role: string;
          download_allowed: boolean;
        };
        selected_assets: Array<{ view_href: string; title: string }>;
      };
    };
    const assetHref = playerPayload.player.selected_asset.view_href;
    expect(playerPayload.player.selected_assets).toEqual([playerPayload.player.selected_asset]);
    expect(playerPayload.player.selected_asset.title).toBe(firstLesson.title);
    expect(playerPayload.player.selected_asset).not.toHaveProperty("file_name");
    expect(playerPayload.player.selected_asset).toMatchObject({
      content_role: "support_material",
      download_allowed: true,
      download_href: assetHref.replace(/\/pages$/, "/download")
    });
    expect(assetHref).toMatch(new RegExp(
      `^/api/library/items/${entitlementId}/assets/[0-9a-f-]{36}/pages$`
    ));
    const pdfManifest = await fetch(`${origin}${assetHref}`, { headers: { cookie: buyer.session } });
    expect(pdfManifest.status, await pdfManifest.clone().text()).toBe(200);
    expect(pdfManifest.headers.get("content-type")).toContain("application/json");
    expect(pdfManifest.headers.get("cache-control")).toBe("private, no-store, max-age=0");
    expect(await pdfManifest.json()).toMatchObject({ page_count: expect.any(Number) });
    const pdfPage = await fetch(`${origin}${assetHref}/1`, { headers: { cookie: buyer.session } });
    expect(pdfPage.status, await pdfPage.clone().text()).toBe(200);
    expect(pdfPage.headers.get("content-type")).toBe("image/png");
    expect(pdfPage.headers.get("cache-control")).toBe("private, no-store, max-age=0");
    expect(pdfPage.headers.get("referrer-policy")).toBe("no-referrer");
    expect(pdfPage.headers.get("x-content-type-options")).toBe("nosniff");
    const rawPdf = await fetch(`${origin}${assetHref.replace(/\/pages$/, "")}`, {
      headers: { cookie: buyer.session }
    });
    expect(rawPdf.status).toBe(404);
    const supportDownload = await fetch(
      `${origin}${playerPayload.player.selected_asset.download_href}`,
      { headers: { cookie: buyer.session } }
    );
    expect(supportDownload.status, await supportDownload.clone().text()).toBe(200);
    expect(supportDownload.headers.get("content-disposition")).toMatch(/^attachment;/);

    const videoPlayer = await fetch(
      `${origin}/api/library/items/${entitlementId}/lessons/${seedManifest.video.lesson_id}`,
      { headers: { cookie: buyer.session } }
    );
    expect(videoPlayer.status, await videoPlayer.clone().text()).toBe(200);
    const videoPlayerPayload = await videoPlayer.json() as {
      player: {
        selected_asset: { view_href: string; media_type: string };
        selected_assets: Array<{ view_href: string; media_type: string }>;
      };
    };
    expect(videoPlayerPayload.player.selected_asset.media_type).toBe("video/mp4");
    expect(videoPlayerPayload.player.selected_assets.map((asset) => asset.media_type))
      .toEqual(["video/mp4", "application/pdf"]);
    const videoAsset = videoPlayerPayload.player.selected_assets.find(
      (asset) => asset.media_type === "video/mp4"
    );
    const finalPdfAsset = videoPlayerPayload.player.selected_assets.find(
      (asset) => asset.media_type === "application/pdf"
    );
    expect(videoAsset).toBeDefined();
    expect(finalPdfAsset).toBeDefined();
    const videoHref = videoAsset!.view_href;
    const finalPdfManifest = await fetch(`${origin}${finalPdfAsset!.view_href}`, {
      headers: { cookie: buyer.session }
    });
    expect(finalPdfManifest.status, await finalPdfManifest.clone().text()).toBe(200);
    expect(finalPdfManifest.headers.get("content-type")).toContain("application/json");
    const finalPdfPage = await fetch(`${origin}${finalPdfAsset!.view_href}/1`, {
      headers: { cookie: buyer.session }
    });
    expect(finalPdfPage.status, await finalPdfPage.clone().text()).toBe(200);
    expect(finalPdfPage.headers.get("content-type")).toBe("image/png");
    const videoRange = await fetch(`${origin}${videoHref}`, {
      headers: { cookie: buyer.session, range: "bytes=0-1023" }
    });
    expect(videoRange.status).toBe(206);
    expect(videoRange.headers.get("content-type")).toBe("video/mp4");
    expect(videoRange.headers.get("accept-ranges")).toBe("bytes");
    expect(videoRange.headers.get("content-range")).toBe(`bytes 0-1023/${seedManifest.video.bytes}`);
    expect((await videoRange.arrayBuffer()).byteLength).toBe(1024);
    const videoHead = await fetch(`${origin}${videoHref}`, {
      method: "HEAD",
      headers: { cookie: buyer.session, range: "bytes=0-31" }
    });
    expect(videoHead.status).toBe(200);
    expect(videoHead.headers.get("content-length")).toBe(String(seedManifest.video.bytes));
    expect(videoHead.headers.get("content-range")).toBeNull();
    expect(videoHead.headers.get("referrer-policy")).toBe("no-referrer");
    const invalidVideoRange = await fetch(`${origin}${videoHref}`, {
      headers: { cookie: buyer.session, range: `bytes=${seedManifest.video.bytes}-` }
    });
    expect(invalidVideoRange.status).toBe(416);
    expect(invalidVideoRange.headers.get("content-range")).toBe(`bytes */${seedManifest.video.bytes}`);
    expect(invalidVideoRange.headers.get("content-length")).toBe("0");
    expect((await invalidVideoRange.arrayBuffer()).byteLength).toBe(0);

    const loadResults = await Promise.all(Array.from({ length: 100 }, async () => {
      const started = performance.now();
      const response = await fetch(`${origin}/api/library`, { headers: { cookie: buyer.session } });
      await response.arrayBuffer();
      return { status: response.status, durationMs: performance.now() - started };
    }));
    expect(loadResults.every((result) => result.status === 200)).toBe(true);
    const p95 = loadResults.map((result) => result.durationMs).sort((left, right) => left - right)[94]!;
    expect(p95).toBeLessThan(10_000);

    const pdfLoad = await Promise.all(Array.from({ length: 100 }, async () => {
      const response = await fetch(`${origin}${assetHref}`, { headers: { cookie: buyer.session } });
      await response.arrayBuffer();
      return response.status;
    }));
    expect(pdfLoad.every((status) => status === 200)).toBe(true);

    const admin = await login(origin, "admin");
    expect(admin.redirect).toBe(`${origin}/dashboard`);
    const adminSellerCsrf = cookieValue(
      cookiePair(admin.setCookies.join(","), "appsell_seller_csrf")
    );
    const adminSellers = await fetch(`${origin}/api/sellers`, {
      headers: { cookie: admin.session }
    });
    expect(adminSellers.status, await adminSellers.clone().text()).toBe(200);
    expect(await adminSellers.json()).toMatchObject({
      sellers: [{ id: sellerId, role: "seller_owner" }]
    });
    const forbiddenAdminOnboarding = await fetch(`${origin}/api/sellers`, {
      method: "POST",
      headers: { cookie: admin.session, origin },
      body: new URLSearchParams({
        _csrf: adminSellerCsrf,
        idempotency_key: randomUUID(),
        slug: `admin-forbidden-${suffix}`,
        display_name: "Admin Must Stay In Demo"
      })
    });
    expect(forbiddenAdminOnboarding.status).toBe(403);
    expect(await forbiddenAdminOnboarding.json()).toEqual({
      error: "demo_admin_capability_forbidden"
    });
    const forbiddenCrossTenant = await fetch(
      `${origin}/api/sellers/${randomUUID()}/products`,
      { headers: { cookie: admin.session } }
    );
    expect(forbiddenCrossTenant.status).toBe(403);
    expect(await forbiddenCrossTenant.json()).toEqual({
      error: "demo_admin_capability_forbidden"
    });
    const adminLibrary = await fetch(`${origin}/api/library`, { headers: { cookie: admin.session } });
    expect(adminLibrary.status, await adminLibrary.clone().text()).toBe(200);
    expect(await adminLibrary.json()).toEqual({ items: [] });
    const forbiddenPdf = await fetch(`${origin}${assetHref}`, { headers: { cookie: admin.session } });
    expect(forbiddenPdf.status).not.toBe(200);
    const forbiddenVideo = await fetch(`${origin}${videoHref}`, {
      headers: { cookie: admin.session, range: "bytes=0-31" }
    });
    expect(forbiddenVideo.status).not.toBe(200);

    browser = await chromium.launch();
    const buyerContext = await browser.newContext({
      viewport: { width: 320, height: 720 },
      reducedMotion: "reduce"
    });
    const buyerPage = await buyerContext.newPage();
    await buyerPage.goto(`${origin}/s/autonomia-demo`);
    await pwExpect(buyerPage.getByRole("link", { name: "Autonom.ia Sell — início" })).toContainText("Autonom.ia Sell");
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "Autonom.ia Demo" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("heading", { level: 3, name: "Codex na prática" })).toBeVisible();
    const storefrontCta = buyerPage.getByRole("link", { name: "Comprar com cupom demo" });
    await pwExpect(storefrontCta).toBeVisible();
    expect((await storefrontCta.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await expectNoHorizontalOverflow(buyerPage);
    await buyerPage.setViewportSize({ width: 844, height: 390 });
    await expectNoHorizontalOverflow(buyerPage);
    await buyerPage.setViewportSize({ width: 390, height: 844 });
    await expectNoHorizontalOverflow(buyerPage);
    await buyerPage.goto(origin);
    await buyerPage.getByRole("link", { name: "Entrar como aluno demo" }).click();
    await buyerPage.waitForURL(`${origin}/library`);
    await pwExpect(buyerPage.getByRole("note")).toContainText(
      "DEMO · Aluno · user.demo@autonomia.site"
    );
    await pwExpect(buyerPage.getByText("Não foi possível validar seu acesso.")).toHaveCount(0);
    await pwExpect(buyerPage.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("heading", { level: 2, name: "Codex na prática", exact: true })).toBeVisible();
    await buyerPage.getByRole("link", { name: "Começar curso" }).click();
    await buyerPage.waitForURL(/\/library\/items\/[0-9a-f-]+\/lessons\/[0-9a-f-]+$/);
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "O que é Codex e quando usar" })).toBeVisible();
    await pwExpect(buyerPage.locator(".protected-pdf-page img")).toHaveCount(1);
    await pwExpect(buyerPage.getByRole("link", { name: "Baixar PDF de apoio" })).toHaveCount(1);
    await pwExpect(buyerPage.locator(".protected-pdf-page > span")).toHaveCount(0);
    await buyerPage.getByRole("button", { name: "Abrir leitor" }).click();
    await pwExpect(buyerPage.getByRole("dialog", { name: /Leitor de/ })).toBeVisible();
    await pwExpect(buyerPage.getByRole("button", { name: "Aumentar zoom" })).toBeVisible();
    await buyerPage.getByRole("button", { name: "Voltar ao curso" }).click();
    await buyerPage.getByRole("button", { name: "Concluir e avançar" }).click();
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "Como preparar contexto, repo e objetivo" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("link", { name: /Baixar PDF/ })).toHaveCount(0);
    await pwExpect(buyerPage.getByText("Download desativado pelo administrador.")).toBeVisible();
    await buyerPage.setViewportSize({ width: 320, height: 720 });
    await buyerPage.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
    await expectNoHorizontalOverflow(buyerPage);
    await buyerPage.evaluate(() => { document.documentElement.style.fontSize = "100%"; });
    await buyerPage.goto(`${origin}/library/items/${entitlementId}/lessons/${seedManifest.video.lesson_id}`);
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "Como operar deploy e rollback" })).toBeVisible();
    await pwExpect(buyerPage.locator('video[aria-label="Codex em 3 passos"]')).toBeVisible();
    await expectNoHorizontalOverflow(buyerPage);
    await buyerPage.goto(`${origin}/library`);
    await buyerPage.getByRole("link", { name: "Continuar curso" }).click();
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "Como preparar contexto, repo e objetivo" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("link", { name: "Produtos" })).toHaveCount(0);
    const backBox = await buyerPage.getByRole("link", { name: "Minha biblioteca" }).boundingBox();
    expect(backBox?.height ?? 0).toBeGreaterThanOrEqual(44);
    const buyerNav = buyerPage.getByRole("navigation", { name: "Navegação móvel" });
    await pwExpect(buyerNav).toBeHidden();
    await expectNoHorizontalOverflow(buyerPage);
    await buyerPage.goto(`${origin}/products`);
    await buyerPage.waitForURL(`${origin}/library`);
    await pwExpect(buyerPage.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
    await pwExpect(buyerPage.getByText("Crie seu espaço de vendas")).toHaveCount(0);
    const browserPage = await buyerPage.request.get(`${origin}${assetHref}/1`);
    expect(browserPage.status()).toBe(200);
    expect(browserPage.headers()["content-type"]).toBe("image/png");
    const browserOriginal = await buyerPage.request.get(
      `${origin}${assetHref.replace(/\/pages$/, "")}`
    );
    expect(browserOriginal.status()).toBe(404);
    await buyerContext.close();

    const adminContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const adminPage = await adminContext.newPage();
    await adminPage.goto(origin);
    await adminPage.getByRole("link", { name: "Entrar como admin demo" }).click();
    await adminPage.waitForURL(`${origin}/dashboard`);
    await pwExpect(adminPage.getByRole("heading", { name: "Seu negócio em um só lugar." })).toBeVisible();
    await pwExpect(adminPage.getByRole("link", { name: "Produtos" }).first()).toBeVisible();
    await adminPage.goto(`${origin}/products`);
    // The demo admin owns exactly one space, so the workspace opens already
    // scoped and offers no picker to choose from.
    await pwExpect(adminPage.getByLabel("Espaço de vendas")).toHaveCount(0);
    const productTitle = `Produto Demo ${suffix}`;
    const editedTitle = `${productTitle} Editado`;
    await adminPage.getByLabel("Nome do produto").fill(productTitle);
    await adminPage.getByLabel("Endereço do produto").fill(`produto-demo-${suffix}`);
    await adminPage.getByLabel("Descrição curta").fill("Criado pelo fluxo integrado da conta admin demo.");
    await adminPage.getByLabel("Nome do curso inicial").fill(`Curso ${suffix}`);
    await adminPage.getByRole("button", { name: "Criar produto e curso" }).click();
    const createdCard = adminPage.locator("article.draft-card").filter({ hasText: productTitle });
    await pwExpect(createdCard.getByRole("heading", { name: productTitle })).toBeVisible();
    await createdCard.getByRole("link", { name: /Continuar edição/ }).click();
    await pwExpect(adminPage.getByRole("heading", { level: 1, name: productTitle })).toBeVisible({
      timeout: 20_000
    });
    const editorialTitle = `${productTitle} Editorial`;
    await adminPage.getByLabel("Nome do produto").fill(editorialTitle);
    await adminPage.getByRole("button", { name: "Salvar rascunho" }).click();
    await pwExpect(adminPage.getByRole("heading", { level: 1, name: editorialTitle })).toBeVisible();
    await pwExpect(adminPage.getByText("Rascunho salvo com segurança.", { exact: true })).toBeVisible();
    await adminPage.locator('a[href="?section=oferta"]').click();
    await adminPage.getByLabel("Nome da oferta").fill(`Oferta ${suffix}`);
    await adminPage.getByLabel("Resumo da oferta").fill("Oferta informativa da demonstração integrada.");
    await adminPage.getByRole("button", { name: "Salvar rascunho" }).click();
    await pwExpect(adminPage.getByText("Rascunho salvo com segurança.", { exact: true })).toBeVisible();
    await adminPage.locator('a[href="?section=produto"]').click();
    await adminPage.getByLabel("Nome do produto").fill(editedTitle);
    await adminPage.getByRole("button", { name: "Salvar rascunho" }).click();
    await pwExpect(adminPage.getByRole("heading", { level: 1, name: editedTitle })).toBeVisible();
    await pwExpect(adminPage.getByText("Rascunho salvo com segurança.", { exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(adminPage);
    await adminPage.goto(`${origin}/library`);
    await pwExpect(adminPage.getByText("Nenhum produto ativo está disponível para esta conta.")).toBeVisible();
    expect((await adminPage.request.get(`${origin}${assetHref}`)).status()).not.toBe(200);
    await adminContext.close();

    await redis.stop();
    redisStopped = true;
    const unavailableSession = await fetch(`${origin}/api/demo-checkout`, {
      method: "POST",
      headers: { cookie: buyer.session, origin },
      body: formData({
        _csrf: checkoutPayload.csrf.complete,
        idempotency_key: randomUUID(),
        coupon_code: "AUTONOMIA100"
      })
    });
    expect(unavailableSession.status).toBe(503);
    expect(await unavailableSession.json()).toEqual({ error: "oauth_store_unavailable" });
  } finally {
    await browser?.close();
    await stopBff(server);
    if (!redisStopped) await redis.stop();
    await rm(join(process.cwd(), distDir), { recursive: true, force: true });
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 120_000);

async function login(origin: string, persona: "admin" | "buyer") {
  const start = await fetch(`${origin}/auth/login?persona=${persona}`, { redirect: "manual" });
  const transaction = cookiePair(start.headers.get("set-cookie"), "appsell_oauth_tx");
  const authorize = await fetch(requiredHeader(start, "location"), { redirect: "manual" });
  const callback = await fetch(requiredHeader(authorize, "location"), {
    redirect: "manual",
    headers: { cookie: transaction }
  });
  expect(callback.status, await callback.clone().text()).toBe(307);
  return {
    redirect: requiredHeader(callback, "location"),
    session: cookiePair(callback.headers.get("set-cookie"), "appsell_session"),
    setCookies: callback.headers.getSetCookie()
  };
}

async function readProductDetail(
  origin: string,
  session: string,
  sellerId: string,
  productId: string
) {
  const response = await fetch(
    `${origin}/api/sellers/${sellerId}/products/${productId}`,
    { headers: { cookie: session } }
  );
  expect(response.status, await response.clone().text()).toBe(200);
  return await response.json() as {
    product: {
      draft_version: number;
      courses: Array<{ id: string; draft_version: number }>;
      offer: { id: string; draft_version: number };
      publication: { id: string; version: number };
      readiness: { can_publish: boolean };
    };
    csrf: { publish: string };
  };
}

function formData(values: Record<string, string>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(values)) form.set(key, value);
  return form;
}

async function waitForCheckoutAccess(origin: string, session: string) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const response = await fetch(`${origin}/api/demo-checkout`, {
      headers: { cookie: session }
    });
    if (response.ok) {
      const payload = await response.json() as { checkout?: { already_owned?: boolean } };
      if (payload.checkout?.already_owned) return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Demo checkout entitlement was not applied before the deadline.");
}

function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(process.execPath, [join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", String(port)], {
    cwd: process.cwd(),
    env: { ...process.env, ...shared, APPSELL_NEXT_DIST_DIR: distDir },
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32"
  });
}

async function waitForBff(process: ChildProcess, port: number) {
  const deadline = Date.now() + 20_000;
  let diagnostics = "";
  process.stderr?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-3_000); });
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`BFF ${port} exited: ${diagnostics}`);
    try {
      if ((await fetch(`http://127.0.0.1:${port}/`)).ok) return;
    } catch {
      // Next.js is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`BFF ${port} did not start: ${diagnostics}`);
}

function cookiePair(setCookie: string | null, name: string) {
  const match = setCookie?.match(new RegExp(`(?:^|,\\s*)(${name}=[^;]+)`));
  if (!match) throw new Error(`Missing ${name} cookie.`);
  return match[1] ?? "";
}

function cookieValue(cookie: string) {
  return cookie.slice(cookie.indexOf("=") + 1);
}

function requiredHeader(response: Response, name: string) {
  const value = response.headers.get(name);
  if (!value) throw new Error(`Missing ${name} header.`);
  return value;
}

function requiredEnvironment(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing integration test setting: ${name}`);
  return value;
}

async function expectNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport);
}

function signalBff(child: ChildProcess, signal: NodeJS.Signals) {
  if (child.pid === undefined) return;
  if (process.platform !== "win32") {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
  }
  try {
    child.kill(signal);
  } catch {
    // The process may have exited while cleanup was in progress.
  }
}

async function stopBff(child: ChildProcess) {
  if (child.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  signalBff(child, "SIGTERM");
  await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 2_000))]);
  if (child.exitCode === null) {
    signalBff(child, "SIGKILL");
    await exited;
  }
}
