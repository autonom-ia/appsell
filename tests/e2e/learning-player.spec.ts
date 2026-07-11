import { expect, test, type Page, type Route } from "@playwright/test";

const entitlementId = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const lessonOne = "a39181dd-c70c-57b3-935d-d97fd834d709";
const lessonTwo = "c27222bd-9a83-5317-a870-f7076bdc8fc5";
const assetId = "a6f01626-8b83-55e5-becc-fbab785f1493";

async function login(page: Page) {
  await page.goto("/");
  const sessionReady = page.waitForResponse((response) => response.url().endsWith("/api/session") && response.status() === 200);
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
  await sessionReady;
}

function playerEnvelope(selectedLessonId: string, version = 1, firstCompleted = false, resumeLessonId = selectedLessonId) {
  return {
    player: {
      seller: { id: "seller" },
      product: { id: "product", slug: "codex-na-pratica", title: "Codex na prática", description: "Curso seguro e direto." },
      publication: { id: "publication", version: 1, source_sha256: "a".repeat(64) },
      entitlement_id: entitlementId,
      enrollment_id: "enrollment",
      course: {
        id: "course", title: "Codex na prática", description: "Curso", position: 0,
        modules: [{ id: "module", title: "Fundamentos", status: "active", position: 0, lessons: [
          { id: lessonOne, title: "O que é Codex", kind: "text", content: "Conteúdo exclusivo da primeira aula.", status: "active", position: 0 },
          { id: lessonTwo, title: "Preparando o contexto", kind: "text", content: "Conteúdo exclusivo da segunda aula.", status: "active", position: 1 }
        ] }]
      },
      selected_lesson_id: selectedLessonId,
      resume_lesson_id: resumeLessonId,
      course_progress_percent: firstCompleted ? 50 : 0,
      state_version: version,
      lesson_progress: firstCompleted ? [{ lesson_id: lessonOne, progress_percent: 100, completed: true, updated_at: "2026-07-11T12:00:00Z" }] : [],
      selected_asset: selectedLessonId === lessonOne
        ? { href: `/api/library/items/${entitlementId}/assets/${assetId}`, title: "Guia da aula", media_type: "application/pdf" }
        : null
    },
    csrf: { progress: "progress-token", complete: "complete-token" }
  };
}

function secondCourseEnvelope() {
  const response = playerEnvelope(lessonTwo, 1, false, lessonTwo);
  response.player.course = {
    id: "second-course", title: "Codex avançado", description: "Segundo curso do produto", position: 1,
    modules: [{
      id: "second-module", title: "Prática", status: "active", position: 0,
      lessons: [{ id: lessonTwo, title: "Preparando o contexto", kind: "text", content: "Conteúdo exclusivo da segunda aula.", status: "active", position: 0 }]
    }]
  };
  return response;
}

async function mockLearningApi(
  page: Page,
  options: {
    libraryResume?: string | null;
    progressEnrollment?: string;
    onSecondLesson?: (route: Route) => Promise<void>;
    onFirstLesson?: (route: Route, requestNumber: number) => Promise<void>;
  } = {}
) {
  const mutations: string[] = [];
  let firstLessonReads = 0;
  await page.route("**/api/library**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/library" && request.method() === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{
        entitlement_id: entitlementId,
        resume_lesson_id: options.libraryResume === undefined ? lessonOne : options.libraryResume,
        seller: { id: "seller", slug: "autonomia-demo", name: "Autonom.ia Demo" },
        product: { id: "product", slug: "codex-na-pratica", title: "Codex na prática", description: "Curso seguro e direto.", publication_id: "publication", publication_version: 1 },
        progress_percent: 0
      }] }) });
    }
    if (request.method() === "PUT" && url.pathname.endsWith(`/lessons/${lessonOne}/progress`)) {
      mutations.push(request.postData() ?? "");
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ progress: {
        entitlement_id: entitlementId, enrollment_id: options.progressEnrollment ?? "enrollment", lesson_id: lessonOne,
        lesson_progress_percent: 1, course_progress_percent: 0, resume_lesson_id: lessonOne,
        state_version: 2, applied: true
      } }) });
    }
    if (request.method() === "POST" && url.pathname.endsWith(`/lessons/${lessonOne}/complete`)) {
      mutations.push(request.postData() ?? "");
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ progress: {
        entitlement_id: entitlementId, enrollment_id: "enrollment", lesson_id: lessonOne,
        lesson_progress_percent: 100, course_progress_percent: 50, resume_lesson_id: lessonOne,
        state_version: 3, applied: true
      } }) });
    }
    if (request.method() === "PUT" && url.pathname.endsWith(`/lessons/${lessonTwo}/progress`)) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ progress: {
        entitlement_id: entitlementId, enrollment_id: "enrollment", lesson_id: lessonTwo,
        lesson_progress_percent: 1, course_progress_percent: 50, resume_lesson_id: lessonTwo,
        state_version: 4, applied: true
      } }) });
    }
    if (request.method() === "GET" && url.pathname.endsWith(`/lessons/${lessonTwo}`) && options.onSecondLesson) {
      return options.onSecondLesson(route);
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
  });
  return mutations;
}

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
  const pdf = page.getByRole("link", { name: /Abrir PDF/ });
  await expect(pdf).toHaveAttribute("href", `/api/library/items/${entitlementId}/assets/${assetId}`);
  await expect(pdf).toHaveAttribute("target", "_blank");
  await expect(page.getByRole("button", { name: "Concluir e avançar" })).toBeEnabled();
  await page.getByRole("button", { name: "Concluir e avançar" }).click();
  await expect(page).toHaveURL(new RegExp(`/lessons/${lessonTwo}$`));
  await expect(page.getByRole("heading", { level: 1, name: "Preparando o contexto" })).toBeFocused();
  expect(mutations.some((body) => body.includes("progress-token") && body.includes("expected_version"))).toBe(true);
  expect(mutations.some((body) => body.includes("complete-token") && body.includes("idempotency_key"))).toBe(true);
  const size = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(size.scroll).toBeLessThanOrEqual(size.client);
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
  await mockLearningApi(page, { progressEnrollment: "foreign-enrollment" });
  await login(page);
  await page.goto(`/library/items/${entitlementId}/lessons/${lessonOne}`);
  await expect(page.getByRole("heading", { level: 1, name: "O que é Codex" })).toBeVisible();
  await expect(page.getByText("A resposta de progresso não pôde ser validada.")).toBeVisible();
});

test("removes delivered content when focus revalidation reports revocation", async ({ page }) => {
  await mockLearningApi(page, { onFirstLesson: async (route, requestNumber) => {
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
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("Conteúdo exclusivo da primeira aula.")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name: "Conteúdo indisponível" })).toBeFocused();
});
