import { expect, test, type Page, type Route } from "@playwright/test";
import { resolve } from "node:path";

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
const protectedVideoPath = resolve(process.cwd(), "output/pdf/codex/codex-em-3-passos.mp4");

type PdfStudyMockStore = {
  revision: number;
  annotations: Array<Record<string, unknown>>;
  bookmarks: number[];
};

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
    onPdfPage?: (route: Route, pageNumber: number, requestNumber: number) => Promise<void>;
    pdfPageCount?: number;
    pdfLinks?: Array<Record<string, unknown>>;
    pdfStudyInitial?: {
      annotations?: Array<Record<string, unknown>>;
      bookmarks?: number[];
    };
    pdfStudyStore?: PdfStudyMockStore;
    onPdfStudyMutation?: (mutation: Record<string, unknown>) => void;
  } = {}
) {
  const mutations: string[] = [];
  let firstLessonReads = 0;
  let secondLessonReads = 0;
  let firstProgressWrites = 0;
  const pdfPageReads = new Map<number, number>();
  const pdfStudyStore = options.pdfStudyStore ?? {
    revision: 0,
    annotations: options.pdfStudyInitial?.annotations ?? [],
    bookmarks: options.pdfStudyInitial?.bookmarks ?? []
  };
  const pdfStudyState = () => ({
    asset_id: assetId,
    asset_version: 1,
    page_count: options.pdfPageCount ?? 2,
    revision: pdfStudyStore.revision,
    annotations: pdfStudyStore.annotations,
    bookmarks: pdfStudyStore.bookmarks,
    updated_at: pdfStudyStore.revision === 0 ? null : new Date().toISOString()
  });
  const handleLearningRoute = async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith(`/assets/${assetId}/study`) && request.method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ state: pdfStudyState(), csrf: "pdf-study-token-for-e2e-0123456789" })
      });
    }
    if (url.pathname.endsWith(`/assets/${assetId}/study`) && request.method() === "POST") {
      const body = request.postDataJSON() as {
        expected_revision: number;
        mutation: Record<string, unknown>;
      };
      if (body.expected_revision !== pdfStudyStore.revision) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "pdf_study_revision_conflict" })
        });
      }
      const mutation = body.mutation;
      if (mutation.action === "annotation.upsert") {
        const annotation = mutation.annotation as Record<string, unknown>;
        pdfStudyStore.annotations = [
          ...pdfStudyStore.annotations.filter((item) => item.id !== annotation.id),
          annotation
        ];
      } else if (mutation.action === "annotation.delete") {
        pdfStudyStore.annotations = pdfStudyStore.annotations
          .filter((item) => item.id !== mutation.id);
      } else if (mutation.action === "bookmark.set") {
        const pageNumber = Number(mutation.page);
        pdfStudyStore.bookmarks = mutation.active
          ? [...new Set([...pdfStudyStore.bookmarks, pageNumber])].sort((left, right) => left - right)
          : pdfStudyStore.bookmarks.filter((item) => item !== pageNumber);
      }
      pdfStudyStore.revision += 1;
      options.onPdfStudyMutation?.(mutation);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ state: pdfStudyState() })
      });
    }
    if (request.method() === "GET" && url.pathname.endsWith(`/assets/${assetId}/pages`)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          asset_id: assetId,
          page_count: options.pdfPageCount ?? 2,
          links: options.pdfLinks ?? []
        })
      });
    }
    if (request.method() === "GET" && /\/pages\/links\/[0-9a-f]{16}\/media$/.test(url.pathname)) {
      return route.fulfill({ status: 200, contentType: "video/mp4", path: protectedVideoPath });
    }
    const pdfPage = /\/pages\/([1-9][0-9]*)$/.exec(url.pathname);
    if (request.method() === "GET" && pdfPage && Number(pdfPage[1]) <= (options.pdfPageCount ?? 2)) {
      const pageNumber = Number(pdfPage[1]);
      const requestNumber = (pdfPageReads.get(pageNumber) ?? 0) + 1;
      pdfPageReads.set(pageNumber, requestNumber);
      if (options.onPdfPage) return options.onPdfPage(route, pageNumber, requestNumber);
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
  let activePageRequests = 0;
  let maxActivePageRequests = 0;
  await mockLearningApi(page, {
    pdfPageCount: 65,
    onPdfPage: async (route) => {
      activePageRequests += 1;
      maxActivePageRequests = Math.max(maxActivePageRequests, activePageRequests);
      try {
        await page.waitForTimeout(5);
        await route.fulfill({ status: 200, contentType: "image/png", body: protectedPng });
      } finally {
        activePageRequests -= 1;
      }
    }
  });
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
  await expect.poll(() => pageRequests.length, { timeout: 10_000 }).toBe(65);
  await expect(reader.getByLabel("Ir para página")).toHaveValue("1");
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(2);
  await expect(reader.locator(".pdf-reader-page-placeholder")).toHaveCount(63);
  expect(maxActivePageRequests).toBe(1);
  expect(pageRequests).toEqual(Array.from({ length: 65 }, (_, index) =>
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/${index + 1}`
  ));

  const beforeNext = pageRequests.length;
  await reader.getByRole("button", { name: "Próxima página" }).click();
  await expect(reader.getByLabel("Ir para página")).toHaveValue("2");
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(3);
  expect(pageRequests).toHaveLength(beforeNext);

  const beforeJump = pageRequests.length;
  await reader.getByLabel("Ir para página").fill("50");
  await reader.getByLabel("Ir para página").press("Enter");
  await expect(reader.getByLabel("Ir para página")).toHaveValue("50");
  await expect(reader.locator(".pdf-reader-page img")).toHaveCount(3);
  expect(pageRequests).toHaveLength(beforeJump);
  await page.waitForTimeout(4_000);

  const beforeContinuousScroll = pageRequests.length;
  await reader.locator('.pdf-reader-page[data-page="52"]').evaluate((element) =>
    element.scrollIntoView({ behavior: "auto", block: "start" })
  );
  await expect(reader.getByLabel("Ir para página")).toHaveValue("52");
  expect(pageRequests).toHaveLength(beforeContinuousScroll);
});

test("prioritizes, retries and reuses protected pages while the reader stays open", async ({ page }) => {
  const requestedPages: number[] = [];
  const requestCounts = new Map<number, number>();
  let readerStarted = false;
  let releaseCurrentPage: (() => void) | undefined;
  const currentPageGate = new Promise<void>((resolveGate) => {
    releaseCurrentPage = resolveGate;
  });

  await mockLearningApi(page, {
    pdfPageCount: 6,
    onPdfPage: async (route, pageNumber, requestNumber) => {
      if (!readerStarted) {
        await route.fulfill({ status: 200, contentType: "image/png", body: protectedPng });
        return;
      }
      requestedPages.push(pageNumber);
      requestCounts.set(pageNumber, (requestCounts.get(pageNumber) ?? 0) + 1);
      if (pageNumber === 1 && requestNumber === 2) await currentPageGate;
      if (pageNumber === 3 && requestNumber === 1) {
        await route.fulfill({
          status: 429,
          headers: { "retry-after": "1" },
          contentType: "application/json",
          body: JSON.stringify({ error: "pdf_viewer_busy" })
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "image/png", body: protectedPng });
    }
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await expect(page.locator(".protected-pdf-preview img")).toBeVisible();

  readerStarted = true;
  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reader = page.getByRole("dialog", { name: "Leitor de Guia da aula" });
  await expect.poll(() => requestedPages).toEqual([1]);
  await page.waitForTimeout(150);
  expect(requestedPages).toEqual([1]);

  releaseCurrentPage?.();
  await expect.poll(() => requestCounts.get(3) ?? 0, { timeout: 5_000 }).toBe(2);
  await expect.poll(() => requestCounts.get(6) ?? 0, { timeout: 8_000 }).toBe(1);
  await expect(reader.getByText(/Não foi possível carregar a página/)).toHaveCount(0);

  const pageOneRequests = requestCounts.get(1);
  await reader.getByLabel("Ir para página").fill("4");
  await reader.getByLabel("Ir para página").press("Enter");
  await expect(reader.getByLabel("Ir para página")).toHaveValue("4");
  await reader.getByLabel("Ir para página").fill("1");
  await reader.getByLabel("Ir para página").press("Enter");
  await expect(reader.getByLabel("Ir para página")).toHaveValue("1");
  expect(requestCounts.get(1)).toBe(pageOneRequests);
  await expect(reader.locator('.pdf-reader-page[data-page="1"] img'))
    .toHaveAttribute("src", /^blob:/);

  await reader.getByRole("button", { name: "Voltar ao curso" }).click();
  await expect(page.locator(".protected-pdf-preview img")).toBeVisible();
  const requestsAfterClose = requestCounts.get(1) ?? 0;
  await page.getByRole("button", { name: "Abrir leitor" }).click();
  await expect.poll(() => requestCounts.get(1) ?? 0).toBeGreaterThan(requestsAfterClose);
});

test("keeps the restored PDF page after the reader measures its viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockLearningApi(page, { pdfPageCount: 65 });
  await login(page);
  const viewHref = `/api/library/items/${entitlementId}/assets/${assetId}/pages`;
  await page.evaluate(({ key }) => {
    window.sessionStorage.setItem(key, JSON.stringify({ version: 1, page: 31, zoom: 1 }));
  }, { key: `sell:protected-pdf:v1:${viewHref}` });
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await expect(page.locator(".protected-pdf-preview img")).toHaveAttribute("src", `${viewHref}/31`);

  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reader = page.getByRole("dialog", { name: "Leitor de Guia da aula" });
  await expect(reader.getByLabel("Ir para página")).toHaveValue("31");
  await page.waitForTimeout(500);
  await expect(reader.getByLabel("Ir para página")).toHaveValue("31");
  await expect(reader.locator('.pdf-reader-page[data-page="31"] img')).toHaveAttribute("src", /^blob:/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  await expect(reader.getByLabel("Ir para página")).toHaveValue("31");
  const mobileWidth = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth
  }));
  expect(mobileWidth.scroll).toBeLessThanOrEqual(mobileWidth.client);
});

test("navigates PDF index links and opens linked video inside the protected reader", async ({ page }) => {
  const internalLinkId = "0123456789abcdef";
  const videoLinkId = "fedcba9876543210";
  await mockLearningApi(page, {
    pdfPageCount: 6,
    pdfLinks: [
      {
        id: internalLinkId,
        page: 1,
        kind: "page",
        target_page: 4,
        rect: { left: 8, top: 12, width: 70, height: 7 }
      },
      {
        id: videoLinkId,
        page: 4,
        kind: "video",
        media_href: `/api/library/items/${entitlementId}/assets/${assetId}/pages/links/${videoLinkId}/media`,
        rect: { left: 12, top: 28, width: 76, height: 26 }
      }
    ]
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reader = page.getByRole("dialog", { name: "Leitor de Guia da aula" });

  await reader.getByRole("button", { name: "Ir para a página 4" }).click();
  await expect(reader.getByLabel("Ir para página")).toHaveValue("4");
  const openVideo = reader.getByRole("button", {
    name: "Reproduzir vídeo complementar da página 4"
  });
  await expect(openVideo).toBeVisible();
  await openVideo.click();

  const videoDialog = page.getByRole("dialog", { name: "Vídeo complementar da página 4" });
  await expect(videoDialog).toBeVisible();
  const video = videoDialog.locator("video");
  await expect(video).toHaveAttribute(
    "src",
    `/api/library/items/${entitlementId}/assets/${assetId}/pages/links/${videoLinkId}/media`
  );
  await expect(video).toHaveAttribute("controlslist", "nodownload noremoteplayback");
  await page.keyboard.press("Escape");
  await expect(videoDialog).toHaveCount(0);
  await expect(reader).toBeVisible();
  await expect(reader.getByLabel("Ir para página")).toHaveValue("4");
});

test("persists vector annotations and favorites without drifting across zoom and resize", async ({
  browser, page
}, testInfo) => {
  const mobile = testInfo.project.name.includes("mobile");
  await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 });
  const studyMutations: Record<string, unknown>[] = [];
  const pdfStudyStore: PdfStudyMockStore = {
    revision: 0,
    annotations: [{
      id: "86000000-0000-4000-8000-000000000002",
      page: 2,
      tool: "highlight",
      color: "#E59819",
      width: 0.022,
      points: [{ x: 0.18, y: 0.25 }, { x: 0.72, y: 0.25 }]
    }],
    bookmarks: [2]
  };
  await mockLearningApi(page, {
    pdfPageCount: 3,
    pdfStudyStore,
    onPdfStudyMutation: (mutation) => studyMutations.push(mutation)
  });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reader = page.getByRole("dialog", { name: "Leitor de Guia da aula" });
  const pen = reader.getByRole("button", { name: /Caneta/ });
  await expect(pen).toBeEnabled();
  await pen.click();

  const pageOne = reader.locator('.pdf-reader-page[data-page="1"]');
  const layer = pageOne.locator(".pdf-study-layer");
  const layerBox = await layer.boundingBox();
  expect(layerBox).not.toBeNull();
  const bounds = layerBox!;
  await page.mouse.move(bounds.x + bounds.width * 0.2, bounds.y + bounds.height * 0.3);
  await page.mouse.down();
  for (let step = 1; step <= 10; step += 1) {
    await page.mouse.move(
      bounds.x + bounds.width * (0.2 + step * 0.045),
      bounds.y + bounds.height * (0.3 + Math.sin(step / 2) * 0.015)
    );
  }
  await page.mouse.up();
  await expect(layer.locator("polyline")).toHaveCount(1);
  await reader.getByRole("button", { name: /Favorito/ }).click();
  await expect(reader.getByRole("button", { name: /Favorito/ })).toHaveAttribute(
    "aria-pressed", "true"
  );

  await reader.getByRole("button", { name: /Desfazer/ }).click();
  await expect(layer.locator("polyline")).toHaveCount(0);
  await reader.getByRole("button", { name: /Refazer/ }).click();
  await expect(layer.locator("polyline")).toHaveCount(1);
  await expect.poll(() => studyMutations.length).toBeGreaterThanOrEqual(4);

  const otherDevice = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1366, height: 768 }
  });
  const otherPage = await otherDevice.newPage();
  await mockLearningApi(otherPage, { pdfPageCount: 3, pdfStudyStore });
  await login(otherPage);
  await otherPage.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await otherPage.getByRole("button", { name: "Abrir leitor" }).click();
  const otherReader = otherPage.getByRole("dialog", { name: "Leitor de Guia da aula" });
  await expect(otherReader.getByRole("button", { name: "Caneta" })).toBeEnabled();
  await expect(otherReader.locator('.pdf-reader-page[data-page="1"] .pdf-study-layer polyline'))
    .toHaveCount(1);
  await expect(otherReader.getByRole("button", { name: "Favorito" }))
    .toHaveAttribute("aria-pressed", "true");
  await otherDevice.close();

  const mark = layer.locator("polyline").first();
  const pageBefore = await pageOne.boundingBox();
  const markBefore = await mark.boundingBox();
  const pointsBefore = await mark.getAttribute("points");
  for (let step = 0; step < 12; step += 1) {
    await reader.getByRole("button", { name: "Aumentar zoom" }).click();
  }
  await expect(reader.getByText("400%", { exact: true })).toBeVisible();
  await expect.poll(async () => (await pageOne.boundingBox())?.width ?? 0).toBeGreaterThan(
    (pageBefore?.width ?? 0) * 3.98
  );
  const pageAfter = await pageOne.boundingBox();
  const markAfter = await mark.boundingBox();
  expect(pageBefore && markBefore && pageAfter && markAfter).toBeTruthy();
  const normalizedBefore = {
    x: (markBefore!.x + markBefore!.width / 2 - pageBefore!.x) / pageBefore!.width,
    y: (markBefore!.y + markBefore!.height / 2 - pageBefore!.y) / pageBefore!.height
  };
  const normalizedAfter = {
    x: (markAfter!.x + markAfter!.width / 2 - pageAfter!.x) / pageAfter!.width,
    y: (markAfter!.y + markAfter!.height / 2 - pageAfter!.y) / pageAfter!.height
  };
  expect(Math.abs(normalizedBefore.x - normalizedAfter.x)).toBeLessThan(0.005);
  expect(Math.abs(normalizedBefore.y - normalizedAfter.y)).toBeLessThan(0.005);
  expect(await mark.getAttribute("points")).toBe(pointsBefore);

  await reader.getByRole("button", { name: /Minhas marcações/ }).click();
  const panel = reader.locator(".pdf-study-panel");
  await expect(panel).toBeVisible();
  await panel.getByRole("button", { name: /Página 2/ }).click();
  await expect(reader.getByLabel("Ir para página")).toHaveValue("2");
  if (!mobile) await reader.getByRole("button", { name: /Minhas marcações/ }).click();

  await reader.getByLabel("Ir para página").fill("1");
  await reader.getByLabel("Ir para página").press("Enter");
  await reader.getByRole("button", { name: "Ajustar à largura" }).click();
  await reader.getByRole("button", { name: "Voltar ao curso" }).click();
  await page.reload();
  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reopened = page.getByRole("dialog", { name: "Leitor de Guia da aula" });
  await expect(reopened.getByRole("button", { name: /Caneta/ })).toBeEnabled();
  await expect(reopened.locator('.pdf-reader-page[data-page="1"] .pdf-study-layer polyline'))
    .toHaveCount(1);
  await expect(reopened.getByRole("button", { name: /Favorito/ })).toHaveAttribute(
    "aria-pressed", "true"
  );

  await reopened.getByRole("button", { name: /Borracha/ }).click();
  await reopened.locator('.pdf-reader-page[data-page="1"] .pdf-study-hit-target')
    .dispatchEvent("pointerdown", { pointerId: 1, pointerType: "mouse", button: 0 });
  await expect(reopened.locator('.pdf-reader-page[data-page="1"] .pdf-study-layer polyline'))
    .toHaveCount(0);
  await reopened.getByRole("button", { name: /Desfazer/ }).click();
  await expect(reopened.locator('.pdf-reader-page[data-page="1"] .pdf-study-layer polyline'))
    .toHaveCount(1);

  await reopened.getByRole("button", { name: "Selecionar" }).click();
  const reopenedLayer = reopened.locator('.pdf-reader-page[data-page="1"] .pdf-study-layer');
  const selectedMark = reopenedLayer.locator("polyline").first();
  const pointsBeforeMove = await selectedMark.getAttribute("points");
  const selectedPoint = await selectedMark.evaluate((element) => {
    const mark = element as SVGPolylineElement;
    const point = mark.getPointAtLength(mark.getTotalLength() / 2);
    const matrix = mark.getScreenCTM();
    if (!matrix) throw new Error("missing annotation transform");
    const projected = new DOMPoint(point.x, point.y).matrixTransform(matrix);
    return { x: projected.x, y: projected.y };
  });
  await page.mouse.move(selectedPoint.x, selectedPoint.y);
  await page.mouse.down();
  await page.mouse.move(
    selectedPoint.x + 18,
    selectedPoint.y + 14,
    { steps: 4 }
  );
  await page.mouse.up();
  await expect.poll(() => selectedMark.getAttribute("points")).not.toBe(pointsBeforeMove);
  await reopened.getByRole("button", { name: "Cor #D73535" }).click();
  await expect(selectedMark).toHaveAttribute("stroke", "#D73535");
  await reopened.getByRole("button", { name: "Espessura 3" }).click();
  await expect(selectedMark).toHaveAttribute("stroke-width", "0.005");
  await reopened.getByRole("button", { name: "Excluir marcação" }).click();
  await expect(reopenedLayer.locator("polyline")).toHaveCount(0);
  await reopened.getByRole("button", { name: "Desfazer" }).click();
  await expect(reopenedLayer.locator("polyline")).toHaveCount(1);
  await reopened.getByRole("button", { name: "Refazer" }).click();
  await expect(reopenedLayer.locator("polyline")).toHaveCount(0);
  await reopened.getByRole("button", { name: "Desfazer" }).click();
  await expect(reopenedLayer.locator("polyline")).toHaveCount(1);

  await page.setViewportSize(mobile ? { width: 844, height: 390 } : { width: 1100, height: 760 });
  await expect(reopened.getByLabel("Ir para página")).toHaveValue("1");
  await expect(reopened.locator('.pdf-reader-page[data-page="1"] .pdf-study-layer polyline'))
    .toHaveCount(1);
  await expect.poll(() => studyMutations.length).toBeGreaterThanOrEqual(13);
  const size = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth
  }));
  expect(size.scroll).toBeLessThanOrEqual(size.client);
});

test("draws with one touch, reserves two touches for zoom and scrolls in hand mode", async ({
  page
}, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "Raw multi-touch proof uses Chromium CDP.");
  await page.setViewportSize({ width: 390, height: 844 });
  await mockLearningApi(page, { pdfPageCount: 3 });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await page.getByRole("button", { name: "Abrir leitor" }).click();
  const reader = page.getByRole("dialog", { name: "Leitor de Guia da aula" });
  await expect(reader.getByRole("button", { name: "Caneta" })).toBeEnabled();
  await reader.getByRole("button", { name: "Caneta" }).click();
  const layer = reader.locator('.pdf-reader-page[data-page="1"] .pdf-study-layer');
  const bounds = await layer.boundingBox();
  expect(bounds).not.toBeNull();

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 2 });
  const touch = async (
    type: "touchStart" | "touchMove" | "touchEnd",
    points: Array<{ x: number; y: number; id: number }>
  ) => cdp.send("Input.dispatchTouchEvent", {
    type,
    touchPoints: points.map((point) => ({ ...point, radiusX: 2, radiusY: 2, force: 1 }))
  });

  const start = { x: bounds!.x + bounds!.width * 0.2, y: bounds!.y + bounds!.height * 0.25 };
  await touch("touchStart", [{ ...start, id: 1 }]);
  await touch("touchMove", [{ x: start.x + 80, y: start.y + 20, id: 1 }]);
  await touch("touchEnd", []);
  await expect(layer.locator("polyline")).toHaveCount(1);

  const center = { x: bounds!.x + bounds!.width / 2, y: bounds!.y + bounds!.height / 2 };
  await touch("touchStart", [
    { x: center.x - 36, y: center.y, id: 2 },
    { x: center.x + 36, y: center.y, id: 3 }
  ]);
  await touch("touchMove", [
    { x: center.x - 92, y: center.y - 24, id: 2 },
    { x: center.x + 92, y: center.y - 24, id: 3 }
  ]);
  await touch("touchEnd", []);
  await expect.poll(async () => Number((await reader.locator(".pdf-reader-zoom-controls > span").textContent())?.replace("%", "")))
    .toBeGreaterThan(100);
  await expect(layer.locator("polyline")).toHaveCount(1);

  await reader.getByRole("button", { name: "Mão" }).click();
  await expect(layer).toHaveCSS("pointer-events", "none");
  const viewport = reader.locator(".pdf-reader-viewport");
  await viewport.evaluate((element) => { element.scrollTop = 0; });
  const viewportBounds = await viewport.boundingBox();
  expect(viewportBounds).not.toBeNull();
  const scrollX = viewportBounds!.x + viewportBounds!.width / 2;
  await touch("touchStart", [{ x: scrollX, y: viewportBounds!.y + 360, id: 4 }]);
  await touch("touchMove", [{ x: scrollX, y: viewportBounds!.y + 130, id: 4 }]);
  await touch("touchEnd", []);
  await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
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
