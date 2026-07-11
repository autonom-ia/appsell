import { expect, test } from "@playwright/test";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByRole("heading", { name: "Bom dia, creator." })).toBeVisible();
}

test("keeps the core journey usable without horizontal overflow", async ({ page }) => {
  await login(page);
  for (const target of [
    { path: "/dashboard", heading: "Bom dia, creator." },
    { path: "/products", heading: "Produtos" },
    { path: "/library", heading: "Minha biblioteca" },
  ]) {
    if (page.url() !== `http://localhost:3000${target.path}`) {
      await page.locator(`a[href="${target.path}"]:visible`).first().click();
      await expect(page).toHaveURL(target.path);
      await expect(page.getByRole("heading", { name: target.heading, exact: true })).toBeVisible();
    }
    const dimensions = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  }
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Outros produtos de Autonom.ia Lab" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Launch Lab" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Growth Systems" })).toHaveCount(1);
});

test("does not render protected preview content for an anonymous request", async ({ request }) => {
  const response = await request.get("/dashboard", { maxRedirects: 0 });
  expect(response.status()).toBe(307);
  expect(response.headers().location).toBe("/");
  const body = await response.text();
  expect(body).not.toContain("Bom dia, creator.");
  expect(body).not.toContain("Creator OS");
});

test("exposes touch-sized mobile navigation and preserves deep links", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await login(page);
  const libraryLink = page.getByRole("navigation", { name: "Navegação móvel" }).getByRole("link", { name: "Minha biblioteca" });
  const box = await libraryLink.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  await libraryLink.click();
  await expect(page).toHaveURL("/library");
  expect((await page.request.get("/api/session")).status()).toBe(200);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
});

test("supports keyboard skip navigation and reduced motion", async ({ page }) => {
  await login(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Bom dia, creator." })).toBeVisible();
  const skipLink = page.getByRole("link", { name: "Pular para o conteúdo" });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#conteudo")).toBeFocused();
  const transitionDuration = await page.locator(".primary-button").first().evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(Number.parseFloat(transitionDuration)).toBeLessThanOrEqual(0.001);
});

test("keeps the protected shell mounted without a client-side session waterfall", async ({ page }) => {
  await login(page);
  let sessionRequests = 0;
  page.on("response", (response) => {
    if (response.url().endsWith("/api/session")) sessionRequests += 1;
  });
  await page.locator('a[href="/products"]:visible').first().click();
  await expect(page.getByRole("heading", { name: "Produtos", exact: true })).toBeVisible();
  expect(sessionRequests).toBe(0);
  await expect(page.locator(".session-loading")).toHaveCount(0);
});

test("keeps seller onboarding recoverable when the domain API is unavailable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto("/products");
  const submit = page.getByRole("button", { name: "Ativar meu espaço" });
  await expect(submit).toBeEnabled();
  await expect(page.getByText("A API está indisponível.")).toBeVisible();
  await page.getByLabel("Nome do negócio").fill("Studio Mobile");
  await page.getByLabel("Endereço público").fill("studio-mobile");
  await submit.click();
  await expect(page.getByText("A API está indisponível.")).toBeVisible();
  await expect(submit).toBeEnabled();
  await expect(page.getByLabel("Nome do negócio")).toHaveValue("Studio Mobile");
});

test("creates a tenant-scoped product draft in the mobile UI", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "interactive product flow runs once in Chromium");
  const sellerId = "52000000-0000-4000-8000-000000000001";
  const otherSellerId = "52000000-0000-4000-8000-000000000002";
  const products: Array<Record<string, unknown>> = [{ id: "53000000-0000-4000-8000-000000000000", slug: "draft-existente", title: "Draft existente", description: "Já persistido.", status: "draft", created_at: "2026-07-11T11:00:00Z", course: { id: "54000000-0000-4000-8000-000000000000", title: "Curso existente", status: "draft" } }];
  products.push({ id: "53000000-0000-4000-8000-000000000010", slug: "publicado", title: "Produto publicado", description: "Snapshot ativo.", status: "published", created_at: "2026-07-11T10:00:00Z", course: { id: "54000000-0000-4000-8000-000000000010", title: "Curso publicado", status: "published" } }, { id: "53000000-0000-4000-8000-000000000020", slug: "despublicado", title: "Produto despublicado", description: "Fora da vitrine.", status: "unpublished", created_at: "2026-07-11T09:00:00Z", course: { id: "54000000-0000-4000-8000-000000000020", title: "Curso despublicado", status: "draft" } });
  let releaseCreate: (() => void) | undefined;
  let markCreateStarted: (() => void) | undefined;
  const createStarted = new Promise<void>((resolve) => { markCreateStarted = resolve; });

  await page.route("**/api/sellers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        sellers: [
          { id: sellerId, slug: "studio-mobile", display_name: "Studio Mobile", role: "seller_owner" },
          { id: otherSellerId, slug: "studio-b", display_name: "Studio B", role: "seller_owner" }
        ]
      })
    });
  });
  await page.route(`**/api/sellers/${sellerId}/products`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ products, next_cursor: null })
      });
      return;
    }
    const posted = route.request().postDataBuffer();
    const input = await new Request(route.request().url(), {
      method: "POST",
      headers: route.request().headers(),
      body: posted ? new Uint8Array(posted) : undefined
    }).formData();
    expect(input.get("_csrf")).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(input.get("idempotency_key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(input.get("slug")).toBe("creator-os-mobile");
    markCreateStarted?.();
    await new Promise<void>((resolve) => { releaseCreate = resolve; });
    products.push({
      id: "53000000-0000-4000-8000-000000000001",
      slug: input.get("slug"),
      title: input.get("product_title"),
      description: input.get("product_description"),
      status: "draft",
      created_at: "2026-07-11T12:00:00Z",
      course: {
        id: "54000000-0000-4000-8000-000000000001",
        title: input.get("course_title"),
        status: "draft"
      }
    });
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ product_id: products[0]?.id, status: "draft", created: true })
    });
  });

  await page.setViewportSize({ width: 360, height: 800 });
  await login(page);
  await page.goto("/products");
  await page.getByLabel("Espaço de vendas").selectOption({ label: "Studio Mobile · @studio-mobile" });
  await expect(page.getByRole("heading", { name: "Estruture sua próxima oferta" })).toBeVisible();
  await expect(page.getByText("Publicado", { exact: true })).toBeVisible();
  await expect(page.getByText("Despublicado", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Editar nova versão" })).toBeVisible();

  await page.getByLabel("Espaço de vendas").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  await page.keyboard.type("Rascunho do tenant A");
  await page.getByRole("article").filter({ hasText: "Draft existente" }).getByRole("link", { name: "Continuar edição" }).click();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Rascunho do tenant A");
  await page.getByLabel("Espaço de vendas").selectOption({ label: "Studio B · @studio-b" });
  await expect(page.getByText("Você tem alterações não salvas.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Espaço de vendas")).toHaveValue(sellerId);
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Rascunho do tenant A");
  await page.getByRole("button", { name: "Descartar e trocar" }).click();
  await expect(page.getByLabel("Espaço de vendas")).toHaveValue(otherSellerId);
  await expect(page.getByLabel("Nome do produto")).toHaveValue("");
  await page.getByLabel("Espaço de vendas").selectOption({ label: "Studio Mobile · @studio-mobile" });

  await page.getByLabel("Nome do produto").focus();
  await page.keyboard.type("Creator OS Mobile");
  await page.getByLabel("Endereço do produto", { exact: false }).fill("creator-os-mobile");
  await page.getByLabel("Descrição curta").fill("Fluxo mobile persistente e privado.");
  await page.getByLabel("Nome do curso inicial").fill("Fundamentos Mobile");
  await page.getByRole("button", { name: "Criar produto e curso" }).click();
  await createStarted;
  await expect(page.getByLabel("Espaço de vendas")).toBeDisabled();
  releaseCreate?.();
  await expect(page.getByRole("heading", { name: "Creator OS Mobile" })).toBeVisible();
  await expect(page.getByText("Fundamentos Mobile", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Espaço de vendas")).toBeEnabled();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("");

  const dimensions = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    targets: [...document.querySelectorAll("button, input, select, textarea")]
      .map((element) => element.getBoundingClientRect())
      .filter((rect) => rect.width > 0 && rect.height > 0)
      .map((rect) => rect.height)
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  expect(dimensions.targets.every((height) => height >= 44)).toBe(true);

  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const scaled = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    offenders: [...document.querySelectorAll("body *")].flatMap((element) => {
      const rect = element.getBoundingClientRect();
      return rect.right > document.documentElement.clientWidth + 1 || rect.left < -1
        ? [{ tag: element.tagName, className: element.className, text: element.textContent?.trim().slice(0, 80), left: rect.left, right: rect.right, width: rect.width }]
        : [];
    }).slice(0, 10)
  }));
  expect(scaled.scroll, JSON.stringify(scaled.offenders)).toBeLessThanOrEqual(scaled.client);
});

test("edits, previews and publishes a tenant-scoped product at 320px", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "authoring flow runs once in Chromium");
  const sellerId = "62000000-0000-4000-8000-000000000001";
  const productId = "63000000-0000-4000-8000-000000000001";
  const courseId = "64000000-0000-4000-8000-000000000001";
  const offerId = "65000000-0000-4000-8000-000000000001";
  let conflictOnce = true;
  const publishKeys: string[] = [];
  const state = { title: "Creator OS", slug: "creator-os", description: "Draft privado.", courseTitle: "Fundamentos", offerTitle: "Oferta Creator", offerSummary: "Termos informativos.", productVersion: 1, courseVersion: 1, offerVersion: 1, status: "draft", hasChanges: true, publication: null as null | { id: string; version: number; published_at: string } };
  const detail = () => ({
    product: {
      id: productId, seller_organization_id: sellerId, slug: state.slug, title: state.title, description: state.description, status: state.status,
      version: state.productVersion, draft_version: state.productVersion,
      course: { id: courseId, title: state.courseTitle, description: "", status: "draft", draft_version: state.courseVersion, position: 1 },
      courses: [{ id: courseId, title: state.courseTitle, description: "", status: "draft", draft_version: state.courseVersion, position: 1 }],
      offer: { id: offerId, title: state.offerTitle, summary: state.offerSummary, status: "draft", version: state.offerVersion, draft_version: state.offerVersion },
      publication: state.publication,
      has_unpublished_changes: state.hasChanges,
      readiness: { can_publish: state.hasChanges, items: [{ key: "server", label: state.hasChanges ? "Validação do servidor concluída" : "Nenhuma alteração nova", ready: state.hasChanges }] }
    },
    csrf: { update: "csrf-update", publish: "csrf-publish" }
  });

  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail()) });
    const posted = route.request().postDataBuffer();
    const form = await new Request(route.request().url(), { method: "PATCH", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined }).formData();
    expect([...form.keys()].sort()).toEqual(["_csrf", "course_id", "course_title", "expected_course_version", "expected_offer_version", "expected_product_version", "idempotency_key", "offer_id", "offer_summary", "offer_title", "product_description", "product_title", "slug"]);
    expect(form.get("_csrf")).toBe("csrf-update");
    expect(form.get("course_id")).toBe(courseId);
    expect(form.get("offer_id")).toBe(offerId);
    if (conflictOnce) {
      conflictOnce = false;
      state.title = "Título concorrente do servidor";
      state.productVersion += 1;
      return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "version_conflict" }) });
    }
    expect(form.get("expected_product_version")).toBe(String(state.productVersion));
    state.title = String(form.get("product_title")); state.slug = String(form.get("slug")); state.description = String(form.get("product_description")); state.courseTitle = String(form.get("course_title")); state.offerTitle = String(form.get("offer_title")); state.offerSummary = String(form.get("offer_summary"));
    state.productVersion += 1; state.courseVersion += 1; state.offerVersion += 1;
    state.hasChanges = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ product_id: productId, course_id: courseId, offer_id: offerId, product_version: state.productVersion, course_version: state.courseVersion, offer_version: state.offerVersion }) });
  });
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/preview`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ preview: true, snapshot: { product: { id: productId, title: state.title, slug: state.slug, description: state.description, status: "draft" }, courses: [{ title: state.courseTitle }], offer: { title: state.offerTitle, summary: state.offerSummary, status: "draft" } }, versions: { product: state.productVersion, course: state.courseVersion, offer: state.offerVersion } }) }));
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/publications`, async (route) => {
    const posted = route.request().postDataBuffer();
    const form = await new Request(route.request().url(), { method: "POST", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined }).formData();
    expect([...form.keys()].sort()).toEqual(["_csrf", "course_versions", "expected_offer_version", "expected_product_version", "idempotency_key", "offer_id"]);
    expect(form.get("_csrf")).toBe("csrf-publish");
    expect(form.get("course_versions")).toBe(JSON.stringify([{ id: courseId, version: state.courseVersion }]));
    publishKeys.push(String(form.get("idempotency_key")));
    if (publishKeys.length === 1) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "upstream_unavailable" }) });
    if (publishKeys.length === 2) expect(publishKeys[1]).toBe(publishKeys[0]);
    if (publishKeys.length === 3) {
      expect(publishKeys[2]).not.toBe(publishKeys[1]);
      return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "version_conflict" }) });
    }
    if (publishKeys.length === 4) expect(publishKeys[3]).not.toBe(publishKeys[2]);
    const publicationVersion = state.publication ? state.publication.version + 1 : 1;
    state.status = "published"; state.hasChanges = false; state.publication = { id: "66000000-0000-4000-8000-000000000001", version: publicationVersion, published_at: "2026-07-11T15:00:00Z" };
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ publication: state.publication, product_version: state.productVersion }) });
  });

  await page.setViewportSize({ width: 320, height: 568 });
  await login(page);
  await page.goto(`/products/${sellerId}/${productId}`);
  await expect(page.getByRole("heading", { name: "Creator OS", exact: true })).toBeVisible();
  await page.getByLabel("Nome do produto").fill("Creator OS 2");
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continuar editando" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "Descartar e sair" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Continuar editando" })).toBeFocused();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(page).toHaveURL(`/products/${sellerId}/${productId}`);
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Creator OS 2");
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  const libraryLink = page.getByRole("navigation", { name: "Navegação móvel" }).getByRole("link", { name: "Minha biblioteca" });
  await libraryLink.click();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(libraryLink).toBeFocused();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Creator OS 2");
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await expect(page.getByRole("button", { name: "Recarregar versão do servidor" })).toBeVisible();
  await page.getByRole("button", { name: "Recarregar versão do servidor" }).click();
  await expect(page.getByText("Versão atualizada; suas alterações locais continuam prontas para novo envio.")).toBeVisible();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Creator OS 2");
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await expect(page.getByText("Rascunho salvo com segurança.")).toBeVisible();

  await page.getByRole("link", { name: /^Oferta/ }).click();
  const offerSummary = page.getByLabel("Resumo da oferta");
  await offerSummary.fill("Oferta sem cobrança conectada.");
  await offerSummary.focus();
  await offerSummary.evaluate((element) => (element as HTMLTextAreaElement).form?.requestSubmit());
  await expect(page.getByText("Rascunho salvo com segurança.")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/products/${sellerId}/${productId}\\?section=oferta$`));
  await expect(offerSummary).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL("/dashboard");
  await page.goForward();
  await expect(page).toHaveURL(new RegExp(`/products/${sellerId}/${productId}\\?section=oferta$`));
  await expect(page.getByLabel("Resumo da oferta")).toHaveValue("Oferta sem cobrança conectada.");
  await page.getByRole("link", { name: /^Preview/ }).click();
  await expect(page.getByRole("heading", { name: "Creator OS 2" })).toBeVisible();
  await expect(page.getByText("Compra indisponível nesta prévia")).toBeVisible();
  await page.getByRole("link", { name: /^Publicar/ }).click();
  await page.getByRole("button", { name: "Publicar primeira versão" }).click();
  await expect(page.getByRole("dialog", { name: "Publicar este snapshot?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Revisar novamente" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "Publicar snapshot" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Revisar novamente" })).toBeFocused();
  await page.getByRole("button", { name: "Publicar snapshot" }).click();
  await expect(page.getByText("A API não respondeu.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Publicar primeira versão" }).click();
  await page.getByRole("button", { name: "Publicar snapshot" }).click();
  await expect(page.getByText("Versão publicada com sucesso.", { exact: false })).toBeVisible();
  await expect(page.getByText("Nenhuma mudança desde o snapshot publicado.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: /Publicar nova versão/ })).toBeDisabled();
  await page.getByRole("link", { name: /^Produto Identidade/ }).click();
  await page.getByLabel("Descrição curta").fill("Novo draft depois do snapshot.");
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await expect(page.getByText("Rascunho salvo com segurança.")).toBeVisible();
  await page.getByRole("link", { name: /^Publicar/ }).click();
  await expect(page.getByRole("button", { name: "Publicar nova versão após v1" })).toBeEnabled();
  await page.getByRole("button", { name: "Publicar nova versão após v1" }).click();
  await page.getByRole("button", { name: "Publicar snapshot" }).click();
  await expect(page.getByText("O produto mudou ou não está pronto.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Publicar nova versão após v1" }).click();
  await page.getByRole("button", { name: "Publicar snapshot" }).click();
  await expect(page.getByText("Versão publicada com sucesso.", { exact: false })).toBeVisible();
  expect(publishKeys).toHaveLength(4);

  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const scaled = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    scrollX: window.scrollX,
    active: document.activeElement instanceof HTMLElement ? document.activeElement.textContent?.trim() || document.activeElement.getAttribute("name") : null,
    clipped: [...document.querySelectorAll("a,button,input,select,textarea")].flatMap((element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || (rect.left >= -1 && rect.right <= document.documentElement.clientWidth + 1)) return [];
      const parentRect = element.parentElement?.getBoundingClientRect();
      const style = getComputedStyle(element);
      return [{
        text: element.textContent?.trim() || element.getAttribute("name"),
        rect: { left: rect.left, right: rect.right, width: rect.width },
        parent: parentRect ? { left: parentRect.left, right: parentRect.right, width: parentRect.width } : null,
        style: { display: style.display, width: style.width, minWidth: style.minWidth, maxWidth: style.maxWidth, paddingInline: style.paddingInline, justifySelf: style.justifySelf }
      }];
    })
  }));
  expect(scaled.scroll).toBeLessThanOrEqual(scaled.client);
  expect(scaled.clipped, JSON.stringify(scaled)).toEqual([]);
  await page.evaluate(() => { document.documentElement.style.fontSize = "100%"; });
  await page.setViewportSize({ width: 568, height: 320 });
  const landscape = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(landscape.scroll).toBeLessThanOrEqual(landscape.client);
});

test("fails publication closed for string readiness and a missing offer", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "blocked readiness runs once in Chromium");
  const sellerId = "72000000-0000-4000-8000-000000000001";
  const productId = "73000000-0000-4000-8000-000000000001";
  const course = { id: "74000000-0000-4000-8000-000000000001", title: "Curso privado", description: "", status: "draft", draft_version: 1, position: 1 };
  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ product: { id: productId, seller_organization_id: sellerId, slug: "produto-sem-oferta", title: "Produto sem oferta", description: "Privado", status: "draft", version: 1, draft_version: 1, course, courses: [course], offer: null, publication: null, has_unpublished_changes: true, readiness: { can_publish: false, items: ["offer_missing"] } }, csrf: { update: "csrf-update", publish: "csrf-publish" } }) }));
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/preview`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ snapshot: { product: { id: productId, title: "Produto sem oferta", slug: "produto-sem-oferta", description: "Privado", status: "draft" }, courses: [{ title: course.title }], offer: null }, versions: { product: 1, course: 1, offer: 0 } }) }));
  await login(page);
  await page.goto(`/products/${sellerId}/${productId}?section=preview`);
  await expect(page.getByText("Oferta ainda não configurada")).toBeVisible();
  await page.getByRole("link", { name: /^Publicar/ }).click();
  await expect(page.getByText("offer_missing")).toBeVisible();
  await expect(page.getByRole("button", { name: "Publicar primeira versão" })).toBeDisabled();
});

test("covers the approved responsive widths", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "viewport matrix runs once in Chromium");
  await login(page);
  for (const width of [320, 360, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ["/", "/dashboard", "/products", "/library"]) {
      await page.goto(path);
      const dimensions = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
      expect(dimensions.scroll, `overflow at ${width}px on ${path}`).toBeLessThanOrEqual(dimensions.client);
      const undersized = await page.locator(".landing a:visible, .landing button:visible, .app-shell a:visible, .app-shell button:visible").evaluateAll((elements) => elements.flatMap((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width < 44 || rect.height < 44 ? [{ text: element.textContent?.trim(), width: rect.width, height: rect.height }] : [];
      }));
      expect(undersized, `undersized targets at ${width}px on ${path}`).toEqual([]);
    }
  }
});
