import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test } from "vitest";

import { deriveCsrfToken } from "../lib/csrf";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;

test.skipIf(!domainApiUrl)("two BFFs persist seller onboarding through the real API and PostgreSQL", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const firstPort = await unusedNonDefaultPort();
  const secondPort = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const firstDist = `.next-domain-${suffix}-one`;
  const secondDist = `.next-domain-${suffix}-two`;
  const csrfKey = randomBytes(32);
  const shared = {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:domain-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: csrfKey.toString("base64"),
    APPSELL_PUBLIC_ORIGIN: `http://localhost:${firstPort}`,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "test-v1", key: randomBytes(32).toString("base64"), active: true }
    ]),
    APPSELL_DOMAIN_API_URL: requiredEnvironment("APPSELL_TEST_DOMAIN_API_URL"),
    APPSELL_INTERNAL_ISSUER: requiredEnvironment("APPSELL_INTERNAL_ISSUER"),
    APPSELL_INTERNAL_AUDIENCE: requiredEnvironment("APPSELL_INTERNAL_AUDIENCE"),
    APPSELL_IDENTITY_ISSUER: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: requiredEnvironment("APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"),
    APPSELL_SERVICE_CREDENTIAL_B64: requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64"),
    APPSELL_LOCAL_IDENTITY_EMAIL: requiredEnvironment("APPSELL_LOCAL_IDENTITY_EMAIL"),
    NEXT_TELEMETRY_DISABLED: "1"
  };
  const first = startBff(firstPort, firstDist, shared);
  let second: ChildProcess | undefined;

  try {
    await waitForBff(first, firstPort);
    second = startBff(secondPort, secondDist, shared);
    await waitForBff(second, secondPort);
    const login = await fetch(`http://127.0.0.1:${firstPort}/auth/login`, { redirect: "manual" });
    const transaction = cookiePair(login.headers.get("set-cookie"), "appsell_oauth_tx");
    const authorize = await fetch(requiredHeader(login, "location"), { redirect: "manual" });
    const callbackUrl = new URL(requiredHeader(authorize, "location"));
    callbackUrl.hostname = "127.0.0.1";
    callbackUrl.port = String(secondPort);
    const callback = await fetch(callbackUrl, {
      redirect: "manual",
      headers: { cookie: transaction }
    });
    expect(callback.status).toBe(307);
    const setCookie = callback.headers.get("set-cookie");
    const session = cookiePair(setCookie, "appsell_session");
    const sellerCsrf = cookiePair(setCookie, "appsell_seller_csrf");
    const sessionId = session.slice(session.indexOf("=") + 1);
    const sellerCsrfToken = sellerCsrf.slice(sellerCsrf.indexOf("=") + 1);
    expect(sellerCsrfToken).toBe(deriveCsrfToken(sessionId, "POST:/api/sellers", csrfKey));
    // Identidade nova, sem nenhuma membership: o login entrega apenas a
    // permissão de criar a primeira loja. As de produto viajavam junto e davam
    // a um comprador real o poder de publicar catálogo (#350).
    for (const ausente of ["appsell_product_csrf", "appsell_product_update_csrf", "appsell_product_publish_csrf"]) {
      expect(setCookie ?? "").not.toContain(`${ausente}=`);
    }
    const cookie = `${session}; ${sellerCsrf}`;

    const empty = await fetch(`http://127.0.0.1:${firstPort}/api/sellers`, {
      headers: { cookie: session }
    });
    expect(empty.status).toBe(200);
    expect(await empty.json()).toEqual({ sellers: [] });

    const slug = `studio-${randomUUID().slice(0, 8)}`;
    const onboarded = await fetch(`http://127.0.0.1:${secondPort}/api/sellers`, {
      method: "POST",
      headers: {
        cookie,
        origin: `http://localhost:${firstPort}`
      },
      body: new URLSearchParams({
        _csrf: sellerCsrfToken,
        idempotency_key: randomUUID(),
        slug,
        display_name: "Studio Integrado"
      })
    });
    expect(onboarded.status, await onboarded.clone().text()).toBe(201);
    // A membership acabou de nascer e as permissões de produto chegam com ela.
    // Sem isto, o primeiro produto levaria 403 e recarregar não resolveria:
    // nenhuma outra rota emite cookie, e o banco recusa um segundo onboarding
    // para quem já é owner ativo.
    const onboardedCookies = onboarded.headers.get("set-cookie") ?? "";
    const productCsrf = cookiePair(onboardedCookies, "appsell_product_csrf");
    const productUpdateCsrf = cookiePair(onboardedCookies, "appsell_product_update_csrf");
    const productPublishCsrf = cookiePair(onboardedCookies, "appsell_product_publish_csrf");
    const productCsrfToken = productCsrf.slice(productCsrf.indexOf("=") + 1);
    expect(productCsrfToken).toBe(deriveCsrfToken(sessionId, "POST:/api/products", csrfKey));
    const productUpdateCsrfToken = productUpdateCsrf.slice(productUpdateCsrf.indexOf("=") + 1);
    expect(productUpdateCsrfToken).toBe(deriveCsrfToken(sessionId, "PATCH:/api/products", csrfKey));
    const productPublishCsrfToken = productPublishCsrf.slice(productPublishCsrf.indexOf("=") + 1);
    expect(productPublishCsrfToken).toBe(
      deriveCsrfToken(sessionId, "POST:/api/products/publications", csrfKey)
    );
    // A partir daqui a sessão carrega o que a loja lhe deu: é assim que o
    // browser segue do onboarding para o primeiro produto sem novo login.
    const cookieComLoja = `${cookie}; ${productCsrf}; ${productUpdateCsrf}; ${productPublishCsrf}`;

    const persisted = await fetch(`http://127.0.0.1:${firstPort}/api/sellers`, {
      headers: { cookie: session }
    });
    expect(persisted.status).toBe(200);
    const view = await persisted.json() as { sellers: Array<{ slug: string; role: string }> };
    expect(view.sellers).toHaveLength(1);
    expect(view.sellers[0]).toMatchObject({ slug, role: "seller_owner" });

    const sellerId = (await onboarded.json() as { id: string }).id;
    const productSlug = `produto-${randomUUID().slice(0, 8)}`;
    const productKey = randomUUID();
    const productBody = new URLSearchParams({
      _csrf: productCsrfToken,
      idempotency_key: productKey,
      slug: productSlug,
      product_title: "Produto Integrado",
      product_description: "Persistido por duas instâncias BFF",
      course_title: "Curso Integrado"
    });
    const created = await fetch(`http://127.0.0.1:${secondPort}/api/sellers/${sellerId}/products`, {
      method: "POST",
      headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
      body: productBody
    });
    expect(created.status, await created.clone().text()).toBe(201);
    const createdPayload = await created.json() as {
      product_id: string; course_id: string; status: string; created: boolean
    };
    expect(createdPayload).toMatchObject({ status: "draft", created: true });

    const replayed = await fetch(`http://127.0.0.1:${firstPort}/api/sellers/${sellerId}/products`, {
      method: "POST",
      headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
      body: productBody
    });
    expect(replayed.status, await replayed.clone().text()).toBe(200);
    expect(await replayed.json()).toMatchObject({ status: "draft", created: false });

    const catalog = await fetch(`http://127.0.0.1:${firstPort}/api/sellers/${sellerId}/products`, {
      headers: { cookie: session }
    });
    expect(catalog.status, await catalog.clone().text()).toBe(200);
    expect(await catalog.json()).toMatchObject({
      products: [{ slug: productSlug, title: "Produto Integrado", course: { title: "Curso Integrado" } }]
    });

    const pagedCatalog = await fetch(`http://127.0.0.1:${secondPort}/api/sellers/${sellerId}/products?cursor=${createdPayload.product_id}`, {
      headers: { cookie: session }
    });
    expect(pagedCatalog.status, await pagedCatalog.clone().text()).toBe(200);
    expect(await pagedCatalog.json()).toEqual({ products: [], next_cursor: null });

    const invalidCursor = await fetch(`http://127.0.0.1:${firstPort}/api/sellers/${sellerId}/products?cursor=invalid`, {
      headers: { cookie: session }
    });
    expect(invalidCursor.status).toBe(422);
    expect(await invalidCursor.json()).toEqual({ error: "invalid_product_input", field: "cursor" });

    const productId = createdPayload.product_id;
    const courseId = createdPayload.course_id;
    expect(productId).toBeTypeOf("string");
    expect(courseId).toBeTypeOf("string");
    const detailUrl = `http://127.0.0.1:${firstPort}/api/sellers/${sellerId}/products/${productId}`;
    const initialDetail = await fetch(detailUrl, { headers: { cookie } });
    expect(initialDetail.status, await initialDetail.clone().text()).toBe(200);
    const initial = await initialDetail.json() as {
      product: { draft_version: number; course: { draft_version: number }; offer: null }
    };
    expect(initial.product.offer).toBeNull();

    const offerId = randomUUID();
    const updateBody = new URLSearchParams({
      _csrf: productUpdateCsrfToken,
      idempotency_key: randomUUID(),
      course_id: courseId!,
      offer_id: offerId,
      expected_product_version: String(initial.product.draft_version),
      expected_course_version: String(initial.product.course.draft_version),
      expected_offer_version: "0",
      slug: productSlug,
      product_title: "Produto Integrado Editado",
      product_description: "Edição persistida por duas instâncias BFF",
      course_title: "Curso Integrado Editado",
      offer_title: "Oferta Principal",
      offer_summary: "Oferta informativa sem autoridade de cobrança"
    });
    const updated = await fetch(detailUrl.replace(`:${firstPort}`, `:${secondPort}`), {
      method: "PATCH",
      headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
      body: updateBody
    });
    expect(updated.status, await updated.clone().text()).toBe(200);
    expect(await updated.json()).toMatchObject({ offer_id: offerId, offer_version: 1 });

    const duplicate = new URLSearchParams(updateBody);
    duplicate.append("product_title", "Duplicado ambiguo");
    const duplicateResponse = await fetch(detailUrl, {
      method: "PATCH",
      headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
      body: duplicate
    });
    expect(duplicateResponse.status).toBe(422);
    expect(await duplicateResponse.json()).toEqual({ error: "invalid_product_input", field: "form" });

    const editedDetail = await fetch(detailUrl, { headers: { cookie } });
    let edited = await editedDetail.json() as {
      product: {
        draft_version: number;
        title: string;
        course: { id: string; draft_version: number };
        offer: { id: string; draft_version: number };
        readiness: { can_publish: boolean };
      }
    };
    expect(edited.product).toMatchObject({
      title: "Produto Integrado Editado",
      offer: { id: offerId, draft_version: 1 },
      readiness: { can_publish: true }
    });

    const curriculumUrl = `${detailUrl}/courses/${courseId}/curriculum`;
    const emptyCurriculum = await fetch(curriculumUrl, { headers: { cookie } });
    expect(emptyCurriculum.status, await emptyCurriculum.clone().text()).toBe(200);
    const curriculum = await emptyCurriculum.json() as {
      course: { draft_version: number };
      modules: unknown[];
      csrf: { create: string; update: string; reorder: string; archive: string };
    };
    expect(curriculum.modules).toEqual([]);
    const moduleKey = randomUUID();
    const moduleBody = new URLSearchParams({
      _csrf: curriculum.csrf.create,
      idempotency_key: moduleKey,
      expected_course_version: String(curriculum.course.draft_version),
      title: "Fundamentos"
    });
    const moduleCreated = await fetch(`${curriculumUrl.replace("/curriculum", "")}/modules`, {
      method: "POST",
      headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
      body: moduleBody
    });
    expect(moduleCreated.status, await moduleCreated.clone().text()).toBe(201);
    const createdModule = await moduleCreated.json() as {
      module_id: string; course_version: number; created: boolean
    };
    expect(createdModule.created).toBe(true);
    const moduleReplay = await fetch(
      `${curriculumUrl.replace(`:${firstPort}`, `:${secondPort}`).replace("/curriculum", "")}/modules`,
      {
        method: "POST",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: moduleBody
      }
    );
    expect(moduleReplay.status, await moduleReplay.clone().text()).toBe(200);
    expect(await moduleReplay.json()).toMatchObject({ module_id: createdModule.module_id, created: false });

    const lessonBody = new URLSearchParams({
      _csrf: curriculum.csrf.create,
      idempotency_key: randomUUID(),
      expected_course_version: String(createdModule.course_version),
      title: "Primeira aula",
      kind: "text",
      content: "Conteúdo textual publicado no snapshot v2."
    });
    const lessonCreated = await fetch(
      `${curriculumUrl.replace("/curriculum", "")}/modules/${createdModule.module_id}/lessons`,
      {
        method: "POST",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: lessonBody
      }
    );
    expect(lessonCreated.status, await lessonCreated.clone().text()).toBe(201);
    const lesson = await lessonCreated.json() as { lesson_id: string; course_version: number };
    expect(lesson.lesson_id).toBeTypeOf("string");

    const maximumLessonContent = `${"é".repeat(4095)}\n\n`;
    expect(Buffer.byteLength(maximumLessonContent)).toBe(8192);
    const lessonUpdated = await fetch(
      `${curriculumUrl.replace("/curriculum", "")}/modules/${createdModule.module_id}/lessons/${lesson.lesson_id}`,
      {
        method: "PATCH",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: new URLSearchParams({
          _csrf: curriculum.csrf.update,
          idempotency_key: randomUUID(),
          expected_course_version: String(lesson.course_version),
          title: "Primeira aula revisada",
          kind: "text",
          content: maximumLessonContent
        })
      }
    );
    expect(lessonUpdated.status, await lessonUpdated.clone().text()).toBe(200);
    const updatedLesson = await lessonUpdated.json() as { course_version: number };

    const oversizedLesson = await fetch(
      `${curriculumUrl.replace("/curriculum", "")}/modules/${createdModule.module_id}/lessons/${lesson.lesson_id}`,
      {
        method: "PATCH",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: new URLSearchParams({
          _csrf: curriculum.csrf.update,
          idempotency_key: randomUUID(),
          expected_course_version: String(updatedLesson.course_version),
          title: "Primeira aula revisada",
          kind: "text",
          content: `${maximumLessonContent}x`
        })
      }
    );
    expect(oversizedLesson.status, await oversizedLesson.clone().text()).toBe(422);

    const secondLessonCreated = await fetch(
      `${curriculumUrl.replace("/curriculum", "")}/modules/${createdModule.module_id}/lessons`,
      {
        method: "POST",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: new URLSearchParams({
          _csrf: curriculum.csrf.create,
          idempotency_key: randomUUID(),
          expected_course_version: String(updatedLesson.course_version),
          title: "Segunda aula",
          kind: "text",
          content: "Conteúdo que permanecerá ativo."
        })
      }
    );
    expect(secondLessonCreated.status, await secondLessonCreated.clone().text()).toBe(201);
    const secondLesson = await secondLessonCreated.json() as { lesson_id: string; course_version: number };

    const reorderedLessons = await fetch(
      `${curriculumUrl.replace("/curriculum", "")}/modules/${createdModule.module_id}/lessons/order`,
      {
        method: "PUT",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: new URLSearchParams({
          _csrf: curriculum.csrf.reorder,
          idempotency_key: randomUUID(),
          expected_course_version: String(secondLesson.course_version),
          ordered_ids: JSON.stringify([secondLesson.lesson_id, lesson.lesson_id])
        })
      }
    );
    expect(reorderedLessons.status, await reorderedLessons.clone().text()).toBe(200);
    const reordered = await reorderedLessons.json() as { course_version: number };

    const archivedLesson = await fetch(
      `${curriculumUrl.replace("/curriculum", "")}/modules/${createdModule.module_id}/lessons/${lesson.lesson_id}/archive`,
      {
        method: "POST",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: new URLSearchParams({
          _csrf: curriculum.csrf.archive,
          idempotency_key: randomUUID(),
          expected_course_version: String(reordered.course_version)
        })
      }
    );
    expect(archivedLesson.status, await archivedLesson.clone().text()).toBe(200);
    const archived = await archivedLesson.json() as { course_version: number };

    const curriculumAfter = await fetch(curriculumUrl, { headers: { cookie } });
    expect(await curriculumAfter.json()).toMatchObject({
      course: { draft_version: archived.course_version },
      modules: [{ id: createdModule.module_id, title: "Fundamentos", lessons: [{ id: secondLesson.lesson_id }] }]
    });
    const refreshedAfterCurriculum = await fetch(detailUrl, { headers: { cookie } });
    edited = await refreshedAfterCurriculum.json() as typeof edited;

    const preview = await fetch(`${detailUrl}/preview`, { headers: { cookie } });
    expect(preview.status, await preview.clone().text()).toBe(200);
    expect(await preview.json()).toMatchObject({
      preview: true,
      state: "unpublished_draft",
      snapshot: {
        schema_version: 4,
        product: { title: "Produto Integrado Editado" },
        courses: [{ modules: [{ title: "Fundamentos", lessons: [{ title: "Segunda aula" }] }] }]
      }
    });

    const publicationBody = new URLSearchParams({
      _csrf: productPublishCsrfToken,
      idempotency_key: randomUUID(),
      offer_id: edited.product.offer.id,
      expected_product_version: String(edited.product.draft_version),
      expected_offer_version: String(edited.product.offer.draft_version),
      course_versions: JSON.stringify([
        { id: edited.product.course.id, version: edited.product.course.draft_version }
      ])
    });
    const publicationUrl = `${detailUrl.replace(`:${firstPort}`, `:${secondPort}`)}/publications`;
    const published = await fetch(
      publicationUrl,
      {
        method: "POST",
        headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
        body: publicationBody
      }
    );
    expect(published.status, await published.clone().text()).toBe(201);
    const publishedPayload = await published.json() as {
      publication_id: string; publication_version: number; source_sha256: string; created: boolean
    };
    expect(publishedPayload).toMatchObject({ publication_version: 1, created: true });

    const retriedPublication = await fetch(publicationUrl, {
      method: "POST",
      headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
      body: publicationBody
    });
    expect(retriedPublication.status, await retriedPublication.clone().text()).toBe(200);
    const retriedPayload = await retriedPublication.json();
    expect(retriedPayload).toMatchObject({
      publication_id: publishedPayload.publication_id,
      publication_version: publishedPayload.publication_version,
      source_sha256: publishedPayload.source_sha256,
      created: false
    });

    const publishedDetailResponse = await fetch(detailUrl, { headers: { cookie } });
    expect(publishedDetailResponse.status, await publishedDetailResponse.clone().text()).toBe(200);
    const publishedDetail = await publishedDetailResponse.json() as {
      product: {
        draft_version: number;
        course: { id: string; draft_version: number };
        offer: { id: string; draft_version: number };
      }
    };

    const duplicateSnapshot = await fetch(publicationUrl, {
      method: "POST",
      headers: { cookie: cookieComLoja, origin: `http://localhost:${firstPort}` },
      body: new URLSearchParams({
        _csrf: productPublishCsrfToken,
        idempotency_key: randomUUID(),
        offer_id: publishedDetail.product.offer.id,
        expected_product_version: String(publishedDetail.product.draft_version),
        expected_offer_version: String(publishedDetail.product.offer.draft_version),
        course_versions: JSON.stringify([{
          id: publishedDetail.product.course.id,
          version: publishedDetail.product.course.draft_version
        }])
      })
    });
    expect(duplicateSnapshot.status).toBe(409);
    expect(await duplicateSnapshot.json()).toEqual({ error: "no_unpublished_changes" });
  } finally {
    await Promise.all([stopBff(first), ...(second ? [stopBff(second)] : [])]);
    await redis.stop();
    await Promise.all([
      rm(join(process.cwd(), firstDist), { recursive: true, force: true }),
      rm(join(process.cwd(), secondDist), { recursive: true, force: true })
    ]);
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 90_000);

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
      // The independent BFF is still starting.
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
