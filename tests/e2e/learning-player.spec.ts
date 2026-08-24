import { expect, test, type Page, type Route } from "@playwright/test";

const entitlementId = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const lessonOne = "a39181dd-c70c-57b3-935d-d97fd834d709";
const lessonTwo = "c27222bd-9a83-5317-a870-f7076bdc8fc5";
const assetId = "a6f01626-8b83-55e5-becc-fbab785f1493";
const videoAssetId = "15944f42-57c4-56ed-a15c-c09a523fd997";
const sellerId = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const productId = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const publicationId = "8418ebec-fecc-5765-b461-305801484915";
const enrollmentId = "14c10114-b046-518d-b6d8-9d9c913d8a8a";
const courseId = "9a4a73d6-bf11-5429-8582-89434000af44";
const moduleId = "b67e8100-c7d0-5ed4-a91c-469846eb2091";
const secondCourseId = "fb740e5c-2d01-5cc2-8397-bb09188d416a";
const secondModuleId = "ebf42728-a9d9-53da-b68f-4146539f9295";
const foreignEnrollmentId = "36a59404-d3f3-5309-95d9-258f4303e687";
const protectedPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAYAAAC56t6BAAAADklEQVR4nGP4DwUMGAwAQuYX6TviV+QAAAAASUVORK5CYII=",
  "base64"
);

async function login(page: Page) {
  await page.goto("/");
  const sessionReady = page.waitForResponse((response) => response.url().endsWith("/api/session") && response.status() === 200);
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
  await sessionReady;
}

function playerEnvelope(selectedLessonId: string, version = 1, firstCompleted = false, resumeLessonId = selectedLessonId) {
  const selectedAsset = selectedLessonId === lessonOne
    ? {
        view_href: `/api/library/items/${entitlementId}/assets/${assetId}/pages`,
        download_href: null,
        title: "Guia da aula",
        media_type: "application/pdf",
        content_role: "course_content",
        download_allowed: false
      }
    : null;
  return {
    player: {
      seller: { id: sellerId },
      product: { id: productId, slug: "codex-na-pratica", title: "Codex na prática", description: "Curso seguro e direto." },
      publication: { id: publicationId, version: 1, source_sha256: "a".repeat(64) },
      entitlement_id: entitlementId,
      enrollment_id: enrollmentId,
      course: {
        id: courseId, title: "Codex na prática", description: "Curso", position: 0,
        modules: [{ id: moduleId, title: "Fundamentos", status: "active", position: 0, lessons: [
          { id: lessonOne, title: "O que é Codex", kind: "text", content: "Conteúdo exclusivo da primeira aula.", status: "active", position: 0 },
          { id: lessonTwo, title: "Preparando o contexto", kind: "text", content: "Conteúdo exclusivo da segunda aula.", status: "active", position: 1 }
        ] }]
      },
      selected_lesson_id: selectedLessonId,
      resume_lesson_id: resumeLessonId,
      course_progress_percent: firstCompleted ? 50 : 0,
      state_version: version,
      lesson_progress: firstCompleted ? [{ lesson_id: lessonOne, progress_percent: 100, completed: true, updated_at: "2026-07-11T12:00:00Z" }] : [],
      selected_asset: selectedAsset,
      selected_assets: selectedAsset === null ? [] : [selectedAsset],
      watermark: { label: "user.demo@autonomia.site · tenant 4df98a2e · abcdef123456" }
    },
    csrf: { progress: "progress-token", complete: "complete-token" }
  };
}

function secondCourseEnvelope() {
  const response = playerEnvelope(lessonTwo, 1, false, lessonTwo);
  response.player.course = {
    id: secondCourseId, title: "Codex avançado", description: "Segundo curso do produto", position: 1,
    modules: [{
      id: secondModuleId, title: "Prática", status: "active", position: 0,
      lessons: [{ id: lessonTwo, title: "Preparando o contexto", kind: "text", content: "Conteúdo exclusivo da segunda aula.", status: "active", position: 0 }]
    }]
  };
  return response;
}

function videoPlayerEnvelope() {
  const response = playerEnvelope(lessonOne);
  const videoAsset = {
    view_href: `/api/library/items/${entitlementId}/assets/${videoAssetId}`,
    download_href: null,
    title: "Codex em 3 passos",
    media_type: "video/mp4",
    content_role: "course_content",
    download_allowed: false
  };
  response.player.selected_assets = [videoAsset, response.player.selected_asset!];
  response.player.selected_asset = videoAsset;
  return response;
}

async function mockLearningApi(
  page: Page,
  options: {
    libraryResume?: string | null;
    progressEnrollment?: string;
    onSecondLesson?: (route: Route, requestNumber: number) => Promise<void>;
    onFirstLesson?: (route: Route, requestNumber: number) => Promise<void>;
    beforeProgressResponse?: () => Promise<void>;
    beforeCompleteResponse?: (lessonId: string) => Promise<void>;
    onFirstProgress?: (route: Route, requestNumber: number, requestedPercent: number) => Promise<void>;
    pdfPageCount?: number;
  } = {}
) {
  const mutations: string[] = [];
  let firstLessonReads = 0;
  let secondLessonReads = 0;
  let firstProgressWrites = 0;
  const handleLearningRoute = async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname.endsWith(`/assets/${assetId}/pages`)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ asset_id: assetId, page_count: options.pdfPageCount ?? 2 })
      });
    }
    const pdfPage = /\/pages\/([1-9][0-9]*)$/.exec(url.pathname);
    if (request.method() === "GET" && pdfPage && Number(pdfPage[1]) <= (options.pdfPageCount ?? 2)) {
      return route.fulfill({ status: 200, contentType: "image/png", body: protectedPng });
    }
    if (["/api/library", "/api/me/products"].includes(url.pathname) && request.method() === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{
        entitlement_id: entitlementId,
        resume_lesson_id: options.libraryResume === undefined ? lessonOne : options.libraryResume,
        seller: { id: sellerId, slug: "autonomia-demo", name: "Autonom.ia Demo" },
        product: { id: productId, slug: "codex-na-pratica", title: "Codex na prática", description: "Curso seguro e direto.", publication_id: publicationId, publication_version: 1 },
        progress_percent: 0
      }] }) });
    }
    if (request.method() === "PUT" && url.pathname.endsWith(`/lessons/${lessonOne}/progress`)) {
      await options.beforeProgressResponse?.();
      const body = request.postData() ?? "";
      mutations.push(body);
      const requestedPercent = Number(/name="progress_percent"\r\n\r\n(\d+)/.exec(body)?.[1] ?? 0);
      firstProgressWrites += 1;
      if (options.onFirstProgress) return options.onFirstProgress(route, firstProgressWrites, requestedPercent);
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ progress: {
        entitlement_id: entitlementId, enrollment_id: options.progressEnrollment ?? enrollmentId, lesson_id: lessonOne,
        lesson_progress_percent: requestedPercent, course_progress_percent: 0, resume_lesson_id: lessonOne,
        state_version: firstProgressWrites + 1, applied: true
      } }) });
    }
    const completedLessonId = [lessonOne, lessonTwo].find((id) =>
      url.pathname.endsWith(`/lessons/${id}/complete`)
    );
    if (request.method() === "POST" && completedLessonId) {
      await options.beforeCompleteResponse?.(completedLessonId);
      mutations.push(request.postData() ?? "");
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ progress: {
        entitlement_id: entitlementId, enrollment_id: enrollmentId, lesson_id: completedLessonId,
        lesson_progress_percent: 100, course_progress_percent: completedLessonId === lessonOne ? 50 : 100,
        resume_lesson_id: completedLessonId, state_version: completedLessonId === lessonOne ? 3 : 5, applied: true
      } }) });
    }
    if (request.method() === "PUT" && url.pathname.endsWith(`/lessons/${lessonTwo}/progress`)) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ progress: {
        entitlement_id: entitlementId, enrollment_id: enrollmentId, lesson_id: lessonTwo,
        lesson_progress_percent: 1, course_progress_percent: 50, resume_lesson_id: lessonTwo,
        state_version: 4, applied: true
      } }) });
    }
    if (request.method() === "GET" && url.pathname.endsWith(`/lessons/${lessonTwo}`) && options.onSecondLesson) {
      secondLessonReads += 1;
      return options.onSecondLesson(route, secondLessonReads);
    }
    if (request.method() === "GET" && url.pathname.endsWith(`/lessons/${lessonTwo}`)) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(playerEnvelope(lessonTwo, 3, true, lessonOne)) });
    }
    if (request.method() === "GET" && url.pathname.endsWith(`/lessons/${lessonOne}`) && options.onFirstLesson) {
      firstLessonReads += 1;
      return options.onFirstLesson(route, firstLessonReads);
    }
    if (request.method() === "GET" && (url.pathname.endsWith(`/items/${entitlementId}`) || url.pathname.endsWith(`/lessons/${lessonOne}`))) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(playerEnvelope(lessonOne)) });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "unexpected_test_route" }) });
  };
  await page.route("**/api/library**", handleLearningRoute);
  await page.route("**/api/me/products**", handleLearningRoute);
  return mutations;
}

test("bounds protected PDF requests for a 65-page reader", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockLearningApi(page, { pdfPageCount: 65 });
  const pageRequests: string[] = [];
  page.on("request", (request) => {
    if (/\/assets\/[^/]+\/pages\/[1-9][0-9]*$/.test(new URL(request.url()).pathname)) {
      pageRequests.push(new URL(request.url()).pathname);
    }
  });

  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await expect(page.locator(".protected-pdf-preview img"))
    .toHaveAttribute("src", `/api/library/items/${entitlementId}/assets/${assetId}/pages/1`);
  pageRequests.length = 0;

  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reader = page.getByRole("dialog", { name: "Leitor de Guia da aula" });
  await expect(reader.locator(".pdf-reader-page")).toHaveCount(65);
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(2);
  await expect(reader.locator(".pdf-reader-page-placeholder")).toHaveCount(63);
  await expect.poll(() => reader.locator(".pdf-reader-page img").evaluateAll((images) =>
    images.every((image) => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0)
  )).toBe(true);
  await page.waitForTimeout(4_000);
  await expect(reader.getByLabel("Ir para página")).toHaveValue("1");
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(2);
  await expect(reader.locator(".pdf-reader-page-placeholder")).toHaveCount(63);
  const initialReaderRequests = new Set(pageRequests);
  expect(initialReaderRequests.size).toBeLessThanOrEqual(2);
  expect(initialReaderRequests.has(
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/2`
  )).toBe(true);
  expect([...initialReaderRequests].every((pathname) => [
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/1`,
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/2`
  ].includes(pathname))).toBe(true);

  const beforeNext = pageRequests.length;
  await reader.getByRole("button", { name: "Próxima página" }).click();
  await expect(reader.getByLabel("Ir para página")).toHaveValue("2");
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(3);
  await expect.poll(() => pageRequests.length).toBeGreaterThan(beforeNext);
  await page.waitForTimeout(4_000);
  await expect(reader.getByLabel("Ir para página")).toHaveValue("2");
  expect(new Set(pageRequests.slice(beforeNext))).toEqual(new Set([
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/3`
  ]));

  const beforeJump = pageRequests.length;
  await reader.getByLabel("Ir para página").fill("50");
  await reader.getByLabel("Ir para página").press("Enter");
  await expect(reader.getByLabel("Ir para página")).toHaveValue("50");
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(3);
  await expect.poll(() => pageRequests.length).toBeGreaterThanOrEqual(beforeJump + 3);
  expect(new Set(pageRequests.slice(beforeJump))).toEqual(new Set([
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/49`,
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/50`,
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/51`
  ]));
  await page.waitForTimeout(4_000);
  await expect(reader.getByLabel("Ir para página")).toHaveValue("50");

  const beforeContinuousScroll = pageRequests.length;
  await reader.locator('.pdf-reader-page[data-page="52"]').evaluate((element) =>
    element.scrollIntoView({ behavior: "auto", block: "start" })
  );
  await expect(reader.getByLabel("Ir para página")).toHaveValue("52");
  await expect.poll(() => pageRequests.length).toBeGreaterThanOrEqual(beforeContinuousScroll + 2);
  expect(new Set(pageRequests.slice(beforeContinuousScroll))).toEqual(new Set([
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/52`,
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/53`
  ]));
});

test("opens the mobile shelf, protects the PDF and completes into the next lesson", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  const mutations = await mockLearningApi(page);
  await login(page);
  await page.goto("/library");
  await expect(page.getByRole("heading", { name: "Codex na prática", exact: true })).toBeVisible();
  await expect(page.getByText("Conteúdo exclusivo da primeira aula.")).toHaveCount(0);
  await page.getByRole("link", { name: /Começar curso/ }).click();
  await expect(page).toHaveURL(new RegExp(`/library/items/${entitlementId}/lessons/${lessonOne}$`));
  const title = page.getByRole("heading", { level: 1, name: "O que é Codex" });
  await expect(title).toBeVisible();
  await expect(title).toBeFocused();
  const pdf = page.locator(".protected-pdf-page img");
  await expect(pdf).toHaveAttribute(
    "src",
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/1`
  );
  await expect(page.getByText("Página 1 de 2", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Baixar PDF/ })).toHaveCount(0);
  await expect(page.getByText("Download desativado pelo administrador.")).toBeVisible();
  await expect(page.locator(".protected-pdf-page > span")).toHaveCount(0);
  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reader = page.getByRole("dialog", { name: "Leitor de Guia da aula" });
  await expect(reader).toBeVisible();
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(2);
  await expect.poll(() => reader.locator(".pdf-reader-page img").nth(1).evaluate((image) =>
    (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0
  )).toBe(true);
  await expect(reader.getByLabel("Ir para página")).toHaveValue("1");
  const zoomIn = reader.getByRole("button", { name: "Aumentar zoom" });
  for (let step = 0; step < 12; step += 1) await zoomIn.click();
  await expect(reader.getByText("400%", { exact: true })).toBeVisible();
  await expect(zoomIn).toBeDisabled();
  await reader.getByRole("button", { name: "Ajustar à largura" }).click();
  await expect(reader.getByText("100%", { exact: true })).toBeVisible();
  await reader.getByLabel("Ir para página").fill("2");
  await reader.getByLabel("Ir para página").press("Enter");
  await expect(reader.getByLabel("Ir para página")).toHaveValue("2");
  await reader.getByRole("button", { name: "Voltar ao curso" }).click();
  await expect(reader).toHaveCount(0);
  await expect(pdf).toHaveAttribute(
    "src",
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/2`
  );
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "O que é Codex" })).toBeVisible();
  await expect(pdf).toHaveAttribute(
    "src",
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/2`
  );
  await page.keyboard.press("Control+p");
  await expect(page.getByText("A impressão deste conteúdo está desativada.")).toBeVisible();
  await page.keyboard.press("Control+s");
  await expect(page.getByText("O arquivo original não está disponível para salvar.")).toBeVisible();
  expect(await page.locator(".protected-pdf-page").evaluate((element) => {
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    return !element.dispatchEvent(event) && event.defaultPrevented;
  })).toBe(true);
  await page.getByRole("button", { name: "Anterior" }).click();
  await page.getByRole("button", { name: "Próxima" }).click();
  await expect(pdf).toHaveAttribute(
    "src",
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/2`
  );
  await expect(page.getByRole("button", { name: "Concluir e avançar" })).toBeEnabled();
  await page.getByRole("button", { name: "Concluir e avançar" }).click();
  await expect(page).toHaveURL(new RegExp(`/lessons/${lessonTwo}$`));
  await expect(page.getByRole("heading", { level: 1, name: "Preparando o contexto" })).toBeFocused();
  expect(mutations.some((body) => body.includes("progress-token") && body.includes("expected_version"))).toBe(true);
  expect(mutations.some((body) => body.includes("complete-token") && body.includes("idempotency_key"))).toBe(true);
  const size = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(size.scroll).toBeLessThanOrEqual(size.client);
});

test("streams protected video on mobile and persists stepped and ended progress", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  const mutations = await mockLearningApi(page, {
    onFirstLesson: async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(videoPlayerEnvelope())
      });
    }
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);

  const video = page.locator('video[aria-label="Codex em 3 passos"]');
  await expect(video).toHaveAttribute("src", `/api/library/items/${entitlementId}/assets/${videoAssetId}`);
  await expect(page.locator(".protected-pdf-page img"))
    .toHaveAttribute("src", `/api/library/items/${entitlementId}/assets/${assetId}/pages/1`);
  await expect(video).toHaveAttribute("controls", "");
  await expect(video).toHaveAttribute("preload", "metadata");
  await expect(video).toHaveAttribute("playsinline", "");
  await page.waitForTimeout(150);
  expect(mutations).toHaveLength(0);

  await video.evaluate((element) => {
    Object.defineProperty(element, "duration", { configurable: true, value: 100 });
    Object.defineProperty(element, "currentTime", { configurable: true, value: 25 });
    element.dispatchEvent(new Event("timeupdate"));
  });
  await expect.poll(() => mutations.some((body) => body.includes('name="progress_percent"\r\n\r\n25'))).toBe(true);

  await video.evaluate((element) => element.dispatchEvent(new Event("ended")));
  await expect.poll(() => mutations.some((body) => body.includes('name="progress_percent"\r\n\r\n100'))).toBe(true);
  const size = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth
  }));
  expect(size.scroll).toBeLessThanOrEqual(size.client);
});

test("retries unacknowledged video progress and preserves the final percentage", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  const mutations = await mockLearningApi(page, {
    onFirstLesson: async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(videoPlayerEnvelope()) });
    },
    onFirstProgress: async (route, requestNumber, requestedPercent) => {
      if (requestNumber === 1) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporary" }) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ progress: {
        entitlement_id: entitlementId, enrollment_id: enrollmentId, lesson_id: lessonOne,
        lesson_progress_percent: requestedPercent, course_progress_percent: requestedPercent === 100 ? 50 : 0,
        resume_lesson_id: lessonOne, state_version: requestNumber + 1, applied: true
      } }) });
    }
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  const video = page.locator('video[aria-label="Codex em 3 passos"]');
  await video.evaluate((element) => {
    Object.defineProperty(element, "duration", { configurable: true, value: 100 });
    Object.defineProperty(element, "currentTime", { configurable: true, value: 25 });
    element.dispatchEvent(new Event("pause"));
  });
  await expect.poll(() => mutations.filter((body) => body.includes('name="progress_percent"\r\n\r\n25')).length, { timeout: 5_000 }).toBe(2);
  await video.evaluate((element) => element.dispatchEvent(new Event("ended")));
  await expect.poll(() => mutations.some((body) => body.includes('name="progress_percent"\r\n\r\n100'))).toBe(true);
});

test("bounds automatic video progress retries during a sustained outage", async ({ page }) => {
  let writes = 0;
  await mockLearningApi(page, {
    onFirstLesson: async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(videoPlayerEnvelope()) });
    },
    onFirstProgress: async (route) => {
      writes += 1;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporary" }) });
    }
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await page.locator('video[aria-label="Codex em 3 passos"]').evaluate((element) => {
    Object.defineProperty(element, "duration", { configurable: true, value: 100 });
    Object.defineProperty(element, "currentTime", { configurable: true, value: 25 });
    element.dispatchEvent(new Event("pause"));
    let currentTime = 25;
    const progression = window.setInterval(() => {
      currentTime += 5;
      Object.defineProperty(element, "currentTime", { configurable: true, value: currentTime });
      element.dispatchEvent(new Event("timeupdate"));
      if (currentTime >= 80) window.clearInterval(progression);
    }, 250);
  });
  await expect.poll(() => writes, { timeout: 8_000 }).toBe(4);
  await page.waitForTimeout(2_300);
  expect(writes).toBe(4);
});

test("clears stale lesson content before a denied cross-resource navigation resolves", async ({ page }) => {
  let release: (() => void) | undefined;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await mockLearningApi(page, { onSecondLesson: async (route) => {
      await delayed;
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_found" }) });
    }
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await expect(page.getByRole("heading", { level: 1, name: "O que é Codex" })).toBeVisible();
  const mobileOutline = page.locator(".player-outline-mobile");
  if (await mobileOutline.isVisible()) await mobileOutline.locator("summary").click();
  await page.getByRole("link", { name: /Preparando o contexto/ }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "O que é Codex" })).toHaveCount(0);
  await expect(page.getByText("Conteúdo exclusivo da primeira aula.")).toHaveCount(0);
  await expect(page.getByText("Carregando aula…")).toBeAttached();
  release?.();
  await expect(page.getByRole("heading", { level: 1, name: "Conteúdo indisponível" })).toBeFocused();
  await expect(page.getByText("Conteúdo exclusivo da primeira aula.")).toHaveCount(0);
  await expect(page.getByText("Conteúdo exclusivo da segunda aula.")).toHaveCount(0);
});

test("uses the server resume lesson to enter the correct course in a multi-course product", async ({ page }) => {
  await mockLearningApi(page, {
    libraryResume: lessonTwo,
    onSecondLesson: async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(secondCourseEnvelope()) });
    }
  });
  await login(page);
  await page.goto("/library");
  const resume = page.getByRole("link", { name: /Começar curso/ });
  await expect(resume).toHaveAttribute("href", `/library/items/${entitlementId}/lessons/${lessonTwo}`);
  await resume.click();
  await expect(page).toHaveURL(new RegExp(`/lessons/${lessonTwo}$`));
  await expect(page.getByRole("heading", { level: 1, name: "Preparando o contexto" })).toBeVisible();
  await expect(page.getByText(/Codex avançado · Prática/)).toBeVisible();
});

test("renders a product without lessons without hiding the shelf", async ({ page }) => {
  await mockLearningApi(page, { libraryResume: null });
  await login(page);
  await page.goto("/library");
  await expect(page.getByRole("heading", { name: "Codex na prática", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Conteúdo indisponível" })).toBeDisabled();
  await expect(page.getByRole("link", { name: /Começar curso|Continuar curso|Rever curso/ })).toHaveCount(0);
});

test("rejects a progress response from another enrollment", async ({ page }) => {
  await mockLearningApi(page, { progressEnrollment: foreignEnrollmentId });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await expect(page.getByRole("heading", { level: 1, name: "O que é Codex" })).toBeVisible();
  await expect(page.getByText("A resposta de progresso não pôde ser validada.")).toBeVisible();
});

test("removes delivered content when focus revalidation reports revocation", async ({ page }) => {
  let releaseProgress: (() => void) | undefined;
  const progressRelease = new Promise<void>((resolve) => { releaseProgress = resolve; });
  let markProgressStarted: (() => void) | undefined;
  const progressStarted = new Promise<void>((resolve) => { markProgressStarted = resolve; });
  await mockLearningApi(page, {
    beforeProgressResponse: async () => {
      markProgressStarted?.();
      await progressRelease;
    },
    onFirstLesson: async (route, requestNumber) => {
      if (requestNumber === 1) {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(playerEnvelope(lessonOne)) });
      } else {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "learning_item_unavailable" }) });
      }
    }
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await expect(page.getByText("Conteúdo exclusivo da primeira aula.")).toBeVisible();
  await progressStarted;
  const revokedResponse = page.waitForResponse((response) =>
    response.request().method() === "GET" &&
    response.url().endsWith(`/lessons/${lessonOne}`) &&
    response.status() === 404
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  releaseProgress?.();
  await revokedResponse;
  await expect(page.getByText("Conteúdo exclusivo da primeira aula.")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name: "Conteúdo indisponível" })).toBeFocused();
});

test("revalidates revocation after completing the final lesson", async ({ page }) => {
  let releaseCompletion: (() => void) | undefined;
  const completionRelease = new Promise<void>((resolve) => { releaseCompletion = resolve; });
  let markCompletionStarted: (() => void) | undefined;
  const completionStarted = new Promise<void>((resolve) => { markCompletionStarted = resolve; });
  await mockLearningApi(page, {
    beforeCompleteResponse: async (completedLessonId) => {
      if (completedLessonId !== lessonTwo) return;
      markCompletionStarted?.();
      await completionRelease;
    },
    onSecondLesson: async (route, requestNumber) => {
      if (requestNumber === 1) {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(playerEnvelope(lessonTwo, 4, true, lessonTwo)) });
      } else {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "learning_item_unavailable" }) });
      }
    }
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonTwo}`);
  await expect(page.getByText("Conteúdo exclusivo da segunda aula.")).toBeVisible();
  await page.getByRole("button", { name: "Concluir curso" }).click();
  await completionStarted;
  const revokedResponse = page.waitForResponse((response) =>
    response.request().method() === "GET" &&
    response.url().endsWith(`/lessons/${lessonTwo}`) &&
    response.status() === 404
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  releaseCompletion?.();
  await revokedResponse;
  await expect(page.getByText("Conteúdo exclusivo da segunda aula.")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name: "Conteúdo indisponível" })).toBeFocused();
});
