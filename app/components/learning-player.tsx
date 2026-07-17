"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";

interface CourseLesson {
  id: string;
  title: string;
  kind: "text";
  content: string;
  status: "active";
  position: number;
}

interface CourseModule {
  id: string;
  title: string;
  status: "active";
  position: number;
  lessons: CourseLesson[];
}

interface LessonProgress {
  lesson_id: string;
  progress_percent: number;
  completed: boolean;
  updated_at: string | null;
}

interface LearningAsset {
  view_href: string;
  download_href: string | null;
  title: string;
  media_type: "application/pdf" | "video/mp4";
  content_role: "course_content" | "support_material";
  download_allowed: boolean;
}

interface LearningPlayerPayload {
  seller: { id: string };
  product: { id: string; slug: string; title: string; description: string };
  publication: { id: string; version: number; source_sha256: string };
  entitlement_id: string;
  enrollment_id: string;
  course: { id: string; title: string; description: string; position: number; modules: CourseModule[] };
  selected_lesson_id: string;
  resume_lesson_id: string;
  course_progress_percent: number;
  state_version: number;
  lesson_progress: LessonProgress[];
  selected_asset: LearningAsset | null;
  selected_assets: LearningAsset[];
  watermark: { label: string };
  csrf: { progress: string; complete: string };
}

interface ProgressMutation {
  entitlement_id: string;
  enrollment_id: string;
  lesson_id: string;
  lesson_progress_percent: number;
  course_progress_percent: number;
  resume_lesson_id: string;
  state_version: number;
  applied: boolean;
}

type PlayerStatus = "loading" | "ready" | "unauthorized" | "forbidden" | "missing" | "conflict" | "unavailable" | "invalid";
type PlayerState =
  | { status: "loading"; requestKey: string }
  | { status: "ready"; requestKey: string; payload: LearningPlayerPayload }
  | { status: Exclude<PlayerStatus, "loading" | "ready">; requestKey: string };

const playerMessages: Record<Exclude<PlayerStatus, "loading" | "ready">, { title: string; body: string }> = {
  unauthorized: { title: "Sua sessão expirou", body: "Entre novamente para continuar o curso." },
  forbidden: { title: "Conteúdo indisponível", body: "Seu acesso a este conteúdo não está ativo." },
  missing: { title: "Conteúdo indisponível", body: "Não foi possível abrir esta aula." },
  conflict: { title: "O curso foi atualizado", body: "Recarregue o curso para continuar na versão publicada mais recente." },
  unavailable: { title: "Não foi possível carregar a aula", body: "Verifique sua conexão e tente novamente." },
  invalid: { title: "Resposta inválida", body: "Os dados da aula não puderam ser validados com segurança." }
};
const TENANT_VIDEO_HREF = /^\/api\/library\/items\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/assets\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/;
const TENANT_PDF_VIEW_HREF = /^\/api\/library\/items\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/assets\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/pages$/;
const TENANT_ASSET_DOWNLOAD_HREF = /^\/api\/library\/items\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/assets\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/download$/;

export function LearningPlayer({ entitlementId, lessonId }: { entitlementId: string; lessonId?: string }) {
  const router = useRouter();
  const desiredKey = `${entitlementId}:${lessonId ?? "resume"}`;
  const [state, setState] = useState<PlayerState>({ status: "loading", requestKey: desiredKey });
  const [mutationState, setMutationState] = useState<"idle" | "opening" | "completing">("idle");
  const [feedback, setFeedback] = useState("");
  const requestRef = useRef<AbortController | null>(null);
  const mutationRef = useRef<AbortController | null>(null);
  const pendingRevalidationRef = useRef(false);
  const idempotencyKeys = useRef(new Map<string, string>());
  const titleRef = useRef<HTMLHeadingElement>(null);
  const errorRef = useRef<HTMLHeadingElement>(null);
  const lastFocusedKey = useRef("");

  const load = useCallback(async (targetLessonId?: string, options: { silent?: boolean } = {}) => {
    const silent = options.silent === true;
    const requestKey = `${entitlementId}:${targetLessonId ?? "resume"}`;
    requestRef.current?.abort();
    if (!silent) mutationRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    if (!silent) {
      setFeedback("");
      setMutationState("idle");
      setState({ status: "loading", requestKey });
    }
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 8_000);
    const base = `/api/library/items/${encodeURIComponent(entitlementId)}`;
    const endpoint = targetLessonId
      ? `${base}/lessons/${encodeURIComponent(targetLessonId)}`
      : base;
    try {
      const response = await fetch(endpoint, { cache: "no-store", signal: controller.signal });
      if (controller.signal.aborted) return;
      if (response.status === 401) return setState({ status: "unauthorized", requestKey });
      if (response.status === 403) return setState({ status: "forbidden", requestKey });
      // A 404 during background revalidation is an access revocation signal,
      // not a transient transport failure. Remove previously delivered content.
      if (response.status === 404) return setState({ status: "missing", requestKey });
      if (response.status === 409) return setState({ status: "conflict", requestKey });
      if (response.status === 503) return silent ? setFeedback("A atualização automática falhou; seu conteúdo continua disponível.") : setState({ status: "unavailable", requestKey });
      if (!response.ok) return silent ? setFeedback("A atualização automática falhou; seu conteúdo continua disponível.") : setState({ status: "invalid", requestKey });
      const responsePayload: unknown = await response.json();
      if (!isLearningPlayerResponse(responsePayload)) {
        return silent ? setFeedback("A atualização automática não pôde ser validada.") : setState({ status: "invalid", requestKey });
      }
      const payload: LearningPlayerPayload = { ...responsePayload.player, csrf: responsePayload.csrf };
      if (payload.entitlement_id !== entitlementId || (targetLessonId && payload.selected_lesson_id !== targetLessonId)) {
        return silent ? setFeedback("A atualização automática não pôde ser validada.") : setState({ status: "invalid", requestKey });
      }
      setState({ status: "ready", requestKey, payload });
      if (!targetLessonId && !silent) {
        router.replace(lessonHref(entitlementId, payload.selected_lesson_id), { scroll: false });
      }
    } catch {
      if (!controller.signal.aborted || timedOut) {
        if (silent) setFeedback("A atualização automática falhou; seu conteúdo continua disponível.");
        else setState({ status: "unavailable", requestKey });
      }
    } finally {
      window.clearTimeout(timeout);
      if (requestRef.current === controller) requestRef.current = null;
    }
  }, [entitlementId, router]);

  useEffect(() => {
    const start = window.setTimeout(() => void load(lessonId), 0);
    return () => {
      window.clearTimeout(start);
      requestRef.current?.abort();
      mutationRef.current?.abort();
    };
  }, [lessonId, load]);

  const visibleState = state.requestKey === desiredKey ? state : { status: "loading", requestKey: desiredKey } as const;
  const payload = visibleState.status === "ready" ? visibleState.payload : null;
  const lessons = useMemo(() => payload ? flattenLessons(payload.course.modules) : [], [payload]);
  const selectedIndex = payload ? lessons.findIndex((entry) => entry.lesson.id === payload.selected_lesson_id) : -1;
  const selected = selectedIndex >= 0 ? lessons[selectedIndex] : undefined;
  const previousId = selectedIndex > 0 ? lessons[selectedIndex - 1]?.lesson.id : undefined;
  const nextId = selectedIndex >= 0 && selectedIndex < lessons.length - 1 ? lessons[selectedIndex + 1]?.lesson.id : undefined;
  const progressByLesson = useMemo(() => new Map(payload?.lesson_progress.map((entry) => [entry.lesson_id, entry]) ?? []), [payload]);
  const selectedProgress = payload ? progressByLesson.get(payload.selected_lesson_id) : undefined;

  useEffect(() => {
    if (!payload || !selected || lastFocusedKey.current === desiredKey) return;
    lastFocusedKey.current = desiredKey;
    requestAnimationFrame(() => {
      window.scrollTo({ top: 0, behavior: "auto" });
      titleRef.current?.focus({ preventScroll: true });
    });
  }, [desiredKey, payload, selected]);

  useEffect(() => {
    if (!payload) return;
    const revalidate = () => {
      if (mutationRef.current) {
        pendingRevalidationRef.current = true;
        return;
      }
      void load(payload.selected_lesson_id, { silent: true });
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") revalidate();
    };
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", onVisibility);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") revalidate();
    }, 60_000);
    return () => {
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearInterval(interval);
    };
  }, [load, payload]);

  function prepareNavigation(event: MouseEvent<HTMLAnchorElement>, targetLessonId: string) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    pendingRevalidationRef.current = false;
    requestRef.current?.abort();
    mutationRef.current?.abort();
    setFeedback("");
    setState({ status: "loading", requestKey: `${entitlementId}:${targetLessonId}` });
  }

  async function saveProgress(current: LearningPlayerPayload, currentLessonId: string, percent: number) {
    if (mutationRef.current) return false;
    requestRef.current?.abort();
    const operation = `progress:${currentLessonId}:${percent}`;
    const controller = new AbortController();
    mutationRef.current = controller;
    setMutationState("opening");
    setFeedback("");
    const form = new FormData();
    form.set("_csrf", current.csrf.progress);
    form.set("idempotency_key", idempotencyKey(idempotencyKeys.current, operation));
    form.set("progress_percent", String(percent));
    form.set("expected_version", String(current.state_version));
    return await mutate(`${lessonEndpoint(entitlementId, currentLessonId)}/progress`, "PUT", form, controller, currentLessonId, current.enrollment_id, false, operation, percent);
  }

  async function completeLesson() {
    if (!payload || !selected || mutationRef.current) return;
    requestRef.current?.abort();
    const currentLessonId = selected.lesson.id;
    const controller = new AbortController();
    mutationRef.current = controller;
    setMutationState("completing");
    setFeedback("");
    const form = new FormData();
    form.set("_csrf", payload.csrf.complete);
    form.set("idempotency_key", idempotencyKey(idempotencyKeys.current, `complete:${currentLessonId}`));
    form.set("expected_version", String(payload.state_version));
    const operation = `complete:${currentLessonId}`;
    await mutate(`${lessonEndpoint(entitlementId, currentLessonId)}/complete`, "POST", form, controller, currentLessonId, payload.enrollment_id, true, operation, 100);
  }

  async function mutate(endpoint: string, method: "PUT" | "POST", form: FormData, controller: AbortController, currentLessonId: string, expectedEnrollmentId: string, complete: boolean, operation: string, expectedProgress: number) {
    try {
      const response = await fetch(endpoint, { method, body: form, cache: "no-store", signal: controller.signal });
      if (controller.signal.aborted) return false;
      if (response.status === 401) {
        setState({ status: "unauthorized", requestKey: desiredKey });
        return false;
      }
      if (response.status === 403 || response.status === 404) {
        setState({ status: "forbidden", requestKey: desiredKey });
        return false;
      }
      if (response.status === 409) {
        setState({ status: "conflict", requestKey: desiredKey });
        return false;
      }
      if (!response.ok) {
        setFeedback("Seu progresso não foi salvo. Tente novamente.");
        return false;
      }
      const responsePayload: unknown = await response.json();
      if (!isProgressMutationResponse(responsePayload) || responsePayload.progress.entitlement_id !== entitlementId ||
        responsePayload.progress.enrollment_id !== expectedEnrollmentId || responsePayload.progress.lesson_id !== currentLessonId) {
        setFeedback("A resposta de progresso não pôde ser validada.");
        return false;
      }
      const result = responsePayload.progress;
      if (result.lesson_progress_percent < expectedProgress) {
        setFeedback("A confirmação do progresso veio incompleta. Tentaremos novamente.");
        return false;
      }
      setState((currentState) => {
        if (currentState.status !== "ready" || currentState.payload.selected_lesson_id !== currentLessonId) return currentState;
        return {
          ...currentState,
          payload: applyProgressMutation(currentState.payload, result)
        };
      });
      idempotencyKeys.current.delete(operation);
      if (complete && nextId) {
        pendingRevalidationRef.current = false;
        setState({ status: "loading", requestKey: `${entitlementId}:${nextId}` });
        router.push(lessonHref(entitlementId, nextId));
      } else {
        setFeedback(complete ? "Curso concluído. Seu progresso foi salvo." : "Progresso salvo.");
      }
      return true;
    } catch {
      if (!controller.signal.aborted) setFeedback("Seu progresso não foi salvo. Verifique a conexão e tente novamente.");
      return false;
    } finally {
      if (mutationRef.current === controller) {
        mutationRef.current = null;
        if (!controller.signal.aborted && pendingRevalidationRef.current) {
          pendingRevalidationRef.current = false;
          void load(currentLessonId, { silent: true });
        }
        setMutationState("idle");
      }
    }
  }

  useEffect(() => {
    if (!payload || !selected || payload.selected_assets.some((asset) => asset.media_type === "video/mp4") ||
      selectedProgress?.completed || (selectedProgress?.progress_percent ?? 0) > 0) return;
    const start = window.setTimeout(() => void saveProgress(payload, selected.lesson.id, 1), 0);
    return () => window.clearTimeout(start);
    // The exact publication state is captured in payload.state_version for this attempt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload?.selected_lesson_id, payload?.state_version, selectedProgress?.completed, selectedProgress?.progress_percent]);

  useEffect(() => {
    if (visibleState.status === "loading" || visibleState.status === "ready") return;
    requestAnimationFrame(() => errorRef.current?.focus({ preventScroll: true }));
  }, [visibleState.requestKey, visibleState.status]);

  if (visibleState.status === "loading") {
    return <PlayerLoading/>;
  }

  if (visibleState.status !== "ready" || !payload || !selected) {
    const status = visibleState.status === "ready" ? "invalid" : visibleState.status;
    const message = playerMessages[status];
    return <section className="learning-player player-state" role="alert" aria-labelledby="player-error-title">
      <p className="eyebrow">Área do aluno</p>
      <h1 id="player-error-title" ref={errorRef} tabIndex={-1}>{message.title}</h1>
      <p>{message.body}</p>
      <div className="player-state-actions">
        {status === "unauthorized" || status === "forbidden" || status === "missing"
          ? <Link className="primary-button" href="/">Voltar para a entrada</Link>
          : <button type="button" className="primary-button" onClick={() => void load(lessonId)}>Tentar novamente</button>}
        <Link className="secondary-button" href="/library">Voltar à biblioteca</Link>
      </div>
    </section>;
  }

  const lessonNumber = selectedIndex + 1;
  const completed = selectedProgress?.completed === true;
  const lessonParagraphs = paragraphs(selected.lesson.content);
  const saveVideoProgress = async (percent: number) => {
    if (percent <= (selectedProgress?.progress_percent ?? 0)) return true;
    return await saveProgress(payload, selected.lesson.id, percent);
  };
  return <div className="learning-player">
    <div className="player-topline">
      <Link href="/library" className="player-back">← <span>Minha biblioteca</span></Link>
      <span>Aula {lessonNumber} de {lessons.length}</span>
    </div>
    <div className="player-layout">
      <article className="player-lesson" aria-labelledby="lesson-title">
        <header>
          <p className="eyebrow">{payload.course.title} · {selected.moduleTitle}</p>
          <h1 id="lesson-title" ref={titleRef} tabIndex={-1}>{selected.lesson.title}</h1>
          {payload.product.description.trim() ? <p>{payload.product.description}</p> : null}
        </header>
        <CourseOutline className="player-outline-mobile" payload={payload} lessons={lessons} progressByLesson={progressByLesson} currentLessonId={selected.lesson.id} onNavigate={prepareNavigation}/>
        <div className="course-progress player-progress">
          <span id="player-progress-label">Progresso do curso: {payload.course_progress_percent}%</span>
          <progress aria-labelledby="player-progress-label" max={100} value={payload.course_progress_percent}>{payload.course_progress_percent}%</progress>
        </div>
        <section className="lesson-content" aria-labelledby="lesson-content-title">
          <h2 id="lesson-content-title" className="sr-only">Conteúdo da aula</h2>
          {lessonParagraphs.length
            ? lessonParagraphs.map((paragraph, index) => <p key={`${selected.lesson.id}:${index}`}>{paragraph}</p>)
            : <p className="lesson-content-empty">Esta aula não possui conteúdo textual.</p>}
        </section>
        {payload.selected_assets.length > 0
          ? payload.selected_assets.map((asset, index) => <LessonMaterial
              key={asset.view_href}
              asset={asset}
              index={index}
              initialProgress={selectedProgress?.progress_percent ?? 0}
              busy={mutationState !== "idle"}
              watermark={payload.watermark.label}
              onVideoProgress={saveVideoProgress}
            />)
          : <p className="asset-unavailable" role="status">Material complementar indisponível para esta aula.</p>}
        <p className="player-feedback" aria-live="polite">{mutationState === "opening" ? "Salvando início da aula…" : mutationState === "completing" ? "Salvando conclusão…" : feedback}</p>
        <nav className="lesson-actions" aria-label="Navegação entre aulas">
          {previousId ? <Link className="secondary-button" href={lessonHref(entitlementId, previousId)} onClick={(event) => prepareNavigation(event, previousId)}>← Aula anterior</Link> : <span/>}
          {completed && nextId
            ? <Link className="primary-button" href={lessonHref(entitlementId, nextId)} onClick={(event) => prepareNavigation(event, nextId)}>Próxima aula →</Link>
            : completed
              ? <Link className="primary-button" href="/library">Curso concluído · voltar à biblioteca</Link>
            : <button type="button" className="primary-button" disabled={mutationState !== "idle" || completed} onClick={() => void completeLesson()}>{completed ? "Aula concluída" : nextId ? "Concluir e avançar" : "Concluir curso"}</button>}
        </nav>
      </article>
      <CourseOutline className="player-outline-desktop" payload={payload} lessons={lessons} progressByLesson={progressByLesson} currentLessonId={selected.lesson.id} onNavigate={prepareNavigation}/>
    </div>
  </div>;
}

function CourseOutline({ className, payload, lessons, progressByLesson, currentLessonId, onNavigate }: {
  className: string;
  payload: LearningPlayerPayload;
  lessons: ReturnType<typeof flattenLessons>;
  progressByLesson: Map<string, LessonProgress>;
  currentLessonId: string;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>, lessonId: string) => void;
}) {
  const currentIndex = lessons.findIndex((entry) => entry.lesson.id === currentLessonId);
  const content = <nav aria-label="Aulas do curso" className="course-outline-list">
    {payload.course.modules.map((module) => <section key={module.id} aria-labelledby={`${className}-${module.id}`}>
      <h3 id={`${className}-${module.id}`}>{module.title}</h3>
      <ol>{module.lessons.map((lesson) => {
        const progress = progressByLesson.get(lesson.id);
        const current = lesson.id === currentLessonId;
        return <li key={lesson.id}>
          <Link href={lessonHref(payload.entitlement_id, lesson.id)} aria-current={current ? "step" : undefined} onClick={(event) => onNavigate(event, lesson.id)}>
            <span aria-hidden="true">{progress?.completed ? "✓" : lesson.position + 1}</span>
            <span>{lesson.title}<small>{progress?.completed ? "Concluída" : current ? "Em andamento" : "Não iniciada"}</small></span>
          </Link>
        </li>;
      })}</ol>
    </section>)}
  </nav>;
  if (className === "player-outline-mobile") {
    return <details className={className}><summary>Conteúdo do curso · Aula {currentIndex + 1} de {lessons.length}</summary><h2 className="sr-only">Conteúdo do curso</h2>{content}</details>;
  }
  return <aside className={className} aria-labelledby="course-outline-title"><h2 id="course-outline-title">Conteúdo do curso</h2>{content}</aside>;
}

function PlayerLoading() {
  return <section className="learning-player player-state player-loading" aria-live="polite" aria-busy="true">
    <p className="sr-only">Carregando aula…</p>
    <div aria-hidden="true"><span/><span/><span/><span/></div>
  </section>;
}

function flattenLessons(modules: CourseModule[]) {
  return modules.flatMap((module) => module.lessons.map((lesson) => ({ lesson, moduleTitle: module.title })));
}

function paragraphs(content: string) {
  return content.split(/\n{2,}/).map((entry) => entry.trim()).filter(Boolean);
}

function lessonHref(entitlementId: string, lessonId: string) {
  return `/library/items/${encodeURIComponent(entitlementId)}/lessons/${encodeURIComponent(lessonId)}`;
}

function lessonEndpoint(entitlementId: string, lessonId: string) {
  return `/api/library/items/${encodeURIComponent(entitlementId)}/lessons/${encodeURIComponent(lessonId)}`;
}

function idempotencyKey(keys: Map<string, string>, operation: string) {
  const current = keys.get(operation);
  if (current) return current;
  const created = crypto.randomUUID();
  keys.set(operation, created);
  return created;
}

function applyProgressMutation(payload: LearningPlayerPayload, mutation: ProgressMutation): LearningPlayerPayload {
  const nextProgress: LessonProgress = {
    lesson_id: mutation.lesson_id,
    progress_percent: mutation.lesson_progress_percent,
    completed: mutation.lesson_progress_percent === 100,
    updated_at: new Date().toISOString()
  };
  const found = payload.lesson_progress.some((entry) => entry.lesson_id === mutation.lesson_id);
  return {
    ...payload,
    state_version: mutation.state_version,
    resume_lesson_id: mutation.resume_lesson_id,
    course_progress_percent: mutation.course_progress_percent,
    lesson_progress: found
      ? payload.lesson_progress.map((entry) => entry.lesson_id === mutation.lesson_id ? nextProgress : entry)
      : [...payload.lesson_progress, nextProgress]
  };
}

export function isLearningPlayerPayload(value: unknown): value is LearningPlayerPayload {
  if (!isRecord(value) || !isStringRecord(value.seller, ["id"]) ||
    !isStringRecord(value.product, ["id", "slug", "title"]) || typeof value.product.description !== "string" ||
    !isStringRecord(value.publication, ["id", "source_sha256"]) ||
    !isPositiveInteger(value.publication.version) || !isNonEmptyString(value.entitlement_id) ||
    !isNonEmptyString(value.enrollment_id) || !isCourse(value.course) ||
    !isNonEmptyString(value.selected_lesson_id) || !isNonEmptyString(value.resume_lesson_id) ||
    !isPercent(value.course_progress_percent) || !isNonNegativeInteger(value.state_version) ||
    !Array.isArray(value.lesson_progress) || !value.lesson_progress.every(isLessonProgress) ||
    !Array.isArray(value.selected_assets) || value.selected_assets.length > 100 ||
    !isStringRecord(value.watermark, ["label"]) || String(value.watermark.label).length > 200 ||
    !isStringRecord(value.csrf, ["progress", "complete"])) return false;
  if (!value.selected_assets.every((asset) => isLearningAsset(asset, value.entitlement_id))) return false;
  const assetHrefs = value.selected_assets.map((asset) => asset.view_href);
  if (new Set(assetHrefs).size !== assetHrefs.length) return false;
  const firstAsset = value.selected_assets[0] ?? null;
  if (value.selected_asset === undefined || !selectedAssetMatches(value.selected_asset, firstAsset)) return false;
  const lessonIds = flattenLessons(value.course.modules).map((entry) => entry.lesson.id);
  return lessonIds.length > 0 && new Set(lessonIds).size === lessonIds.length &&
    lessonIds.includes(value.selected_lesson_id) && lessonIds.includes(value.resume_lesson_id) &&
    value.lesson_progress.every((entry) => lessonIds.includes(entry.lesson_id)) &&
    new Set(value.lesson_progress.map((entry) => entry.lesson_id)).size === value.lesson_progress.length;
}

function isLearningAsset(value: unknown, entitlementId: unknown): value is LearningAsset {
  if (!isStringRecord(value, ["view_href", "title"]) ||
    !["application/pdf", "video/mp4"].includes(String(value.media_type)) ||
    !["course_content", "support_material"].includes(String(value.content_role)) ||
    typeof value.download_allowed !== "boolean" ||
    (value.download_href !== null && typeof value.download_href !== "string")) return false;
  const assetHref = String(value.view_href).match(value.media_type === "application/pdf"
    ? TENANT_PDF_VIEW_HREF
    : TENANT_VIDEO_HREF);
  if (assetHref === null || assetHref[1] !== entitlementId) return false;
  const downloadable = value.media_type === "application/pdf" &&
    value.content_role === "support_material" && value.download_allowed;
  if (!downloadable) return value.download_href === null;
  const downloadHref = String(value.download_href).match(TENANT_ASSET_DOWNLOAD_HREF);
  return downloadHref !== null && downloadHref[1] === entitlementId &&
    downloadHref[2] === assetHref[2];
}

function selectedAssetMatches(value: unknown, firstAsset: LearningAsset | null) {
  if (firstAsset === null) return value === null;
  return isLearningAsset(value, assetEntitlementId(firstAsset)) &&
    value.view_href === firstAsset.view_href && value.title === firstAsset.title &&
    value.media_type === firstAsset.media_type &&
    value.content_role === firstAsset.content_role &&
    value.download_allowed === firstAsset.download_allowed &&
    value.download_href === firstAsset.download_href;
}

function assetEntitlementId(asset: LearningAsset) {
  return asset.view_href.match(asset.media_type === "application/pdf"
    ? TENANT_PDF_VIEW_HREF
    : TENANT_VIDEO_HREF)?.[1];
}

function LessonMaterial({ asset, index, initialProgress, busy, watermark, onVideoProgress }: {
  asset: LearningAsset;
  index: number;
  initialProgress: number;
  busy: boolean;
  watermark: string;
  onVideoProgress: (percent: number) => Promise<boolean>;
}) {
  const video = asset.media_type === "video/mp4";
  const support = asset.content_role === "support_material";
  const titleId = `lesson-material-title-${index}`;
  return <aside className={`lesson-material ${video ? "lesson-video" : ""}`} aria-labelledby={titleId}>
    <div>
      <p className="eyebrow">{video ? "Vídeo da aula" : support ? "Material de apoio" : "Conteúdo do curso"}</p>
      <h2 id={titleId}>{asset.title}</h2>
      {video
        ? <p>Vídeo privado. O progresso é salvo em etapas enquanto você assiste.</p>
        : asset.download_allowed
          ? <p>O administrador permitiu o download deste PDF de apoio.</p>
          : <p>PDF protegido para consulta dentro da área do aluno. Download oficial e impressão não são disponibilizados.</p>}
    </div>
    {video
      ? <ProtectedVideo asset={asset} initialProgress={initialProgress} busy={busy} onProgress={onVideoProgress}/>
      : <ProtectedPdf asset={asset} watermark={watermark}/>}
  </aside>;
}

function ProtectedPdf({ asset, watermark }: { asset: LearningAsset; watermark: string }) {
  const [manifest, setManifest] = useState<{ pageCount: number } | "loading" | "error">("loading");
  const [pageNumber, setPageNumber] = useState(1);
  const [imageError, setImageError] = useState(false);
  const [policyFeedback, setPolicyFeedback] = useState("");

  const loadManifest = useCallback(async () => {
    setManifest("loading");
    setPolicyFeedback("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(asset.view_href, {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal
      });
      const payload: unknown = response.ok ? await response.json() : null;
      const expectedAssetId = asset.view_href.match(TENANT_PDF_VIEW_HREF)?.[2];
      if (!validPdfManifest(payload, expectedAssetId)) throw new Error("invalid_pdf_manifest");
      setManifest({ pageCount: payload.page_count });
      setPageNumber(1);
      setImageError(false);
    } catch {
      if (!controller.signal.aborted) setManifest("error");
      else setManifest("error");
    } finally {
      window.clearTimeout(timeout);
    }
  }, [asset.view_href]);

  useEffect(() => {
    const start = window.setTimeout(() => void loadManifest(), 0);
    return () => window.clearTimeout(start);
  }, [loadManifest]);

  useEffect(() => {
    const blockProtectedDocumentShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || !["p", "s"].includes(event.key.toLowerCase())) return;
      event.preventDefault();
      setPolicyFeedback(event.key.toLowerCase() === "p"
        ? "A impressão deste conteúdo está desativada."
        : "O arquivo original não está disponível para salvar.");
    };
    window.addEventListener("keydown", blockProtectedDocumentShortcut, { capture: true });
    return () => window.removeEventListener("keydown", blockProtectedDocumentShortcut, { capture: true });
  }, []);

  const pageCount = typeof manifest === "object" ? manifest.pageCount : 0;
  const selectPage = (nextPage: number) => {
    if (nextPage < 1 || nextPage > pageCount) return;
    setImageError(false);
    setPageNumber(nextPage);
    setPolicyFeedback("");
  };

  return <div className="protected-pdf">
    {manifest === "loading"
      ? <p className="protected-pdf-state" role="status">Preparando visualização protegida…</p>
      : manifest === "error"
        ? <div className="protected-pdf-state" role="alert"><p>Não foi possível preparar este PDF.</p><button type="button" className="secondary-button" onClick={() => void loadManifest()}>Tentar novamente</button></div>
        : <>
            <div
              className="protected-pdf-page"
              onContextMenu={(event) => event.preventDefault()}
              aria-label={`${asset.title}, página ${pageNumber} de ${pageCount}`}
            >
              {imageError
                ? <div className="protected-pdf-state" role="alert"><p>Não foi possível carregar esta página.</p><button type="button" className="secondary-button" onClick={() => setImageError(false)}>Tentar novamente</button></div>
                // Private, authenticated, no-store raster pages cannot use the optimizing image proxy.
                // eslint-disable-next-line @next/next/no-img-element
                : <img
                    src={`${asset.view_href}/${pageNumber}`}
                    alt={`${asset.title} — página ${pageNumber} de ${pageCount}`}
                    draggable={false}
                    loading="eager"
                    referrerPolicy="no-referrer"
                    onDragStart={(event) => event.preventDefault()}
                    onError={() => setImageError(true)}
                  />}
              <span aria-hidden="true">{watermark}</span>
            </div>
            <nav className="protected-pdf-pagination" aria-label="Páginas do PDF">
              <button type="button" className="secondary-button" disabled={pageNumber === 1} onClick={() => selectPage(pageNumber - 1)}>← Anterior</button>
              <span aria-live="polite">Página {pageNumber} de {pageCount}</span>
              <button type="button" className="secondary-button" disabled={pageNumber === pageCount} onClick={() => selectPage(pageNumber + 1)}>Próxima →</button>
            </nav>
          </>}
    {asset.download_href
      ? <a className="secondary-button" href={asset.download_href}>Baixar PDF de apoio</a>
      : <p className="asset-policy-note" role="note">Download desativado pelo administrador.</p>}
    <p className="asset-policy-feedback" aria-live="polite">{policyFeedback}</p>
  </div>;
}

function validPdfManifest(
  value: unknown,
  expectedAssetId: string | undefined
): value is { asset_id: string; page_count: number } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).sort().join(",") === "asset_id,page_count" &&
    typeof expectedAssetId === "string" && record.asset_id === expectedAssetId &&
    Number.isSafeInteger(record.page_count) && Number(record.page_count) >= 1 &&
    Number(record.page_count) <= 100;
}

function ProtectedVideo({ asset, initialProgress, busy, onProgress }: {
  asset: { view_href: string; title: string };
  initialProgress: number;
  busy: boolean;
  onProgress: (percent: number) => Promise<boolean>;
}) {
  const lastAcknowledged = useRef(initialProgress);
  const pending = useRef<number | null>(null);
  const sending = useRef(false);
  const automaticRetries = useRef(0);
  const retriesExhausted = useRef(false);
  const retryTimer = useRef<number | null>(null);
  const mounted = useRef(true);
  const busyRef = useRef(busy);
  const onProgressRef = useRef(onProgress);
  const flushRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    busyRef.current = busy;
    onProgressRef.current = onProgress;
    if (!busy) flushRef.current();
  }, [busy, onProgress]);

  useEffect(() => {
    lastAcknowledged.current = 0;
    pending.current = null;
    sending.current = false;
    automaticRetries.current = 0;
    retriesExhausted.current = false;
    if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
    retryTimer.current = null;
  }, [asset.view_href]);

  useEffect(() => {
    lastAcknowledged.current = Math.max(lastAcknowledged.current, initialProgress);
    if ((pending.current ?? 0) <= lastAcknowledged.current) pending.current = null;
  }, [initialProgress]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
    };
  }, []);

  const flush = useCallback(() => {
    const scheduleFlush = (delay: number) => {
      if (!mounted.current || retryTimer.current !== null) return;
      retryTimer.current = window.setTimeout(() => {
        retryTimer.current = null;
        flushRef.current();
      }, delay);
    };
    const percent = pending.current;
    if (!mounted.current || busyRef.current || sending.current || retryTimer.current !== null ||
      retriesExhausted.current || percent === null || percent <= lastAcknowledged.current) return;
    sending.current = true;
    void onProgressRef.current(percent).then((acknowledged) => {
      if (!mounted.current) return;
      sending.current = false;
      if (acknowledged) {
        lastAcknowledged.current = Math.max(lastAcknowledged.current, percent);
        automaticRetries.current = 0;
        retriesExhausted.current = false;
        if ((pending.current ?? 0) <= lastAcknowledged.current) pending.current = null;
        if (pending.current !== null) scheduleFlush(0);
        return;
      }
      if (automaticRetries.current < 3) {
        automaticRetries.current += 1;
        scheduleFlush(2_000);
      } else {
        retriesExhausted.current = true;
      }
    }).catch(() => {
      if (!mounted.current) return;
      sending.current = false;
      if (automaticRetries.current < 3) {
        automaticRetries.current += 1;
        scheduleFlush(2_000);
      } else {
        retriesExhausted.current = true;
      }
    });
  }, []);

  useEffect(() => {
    flushRef.current = flush;
  }, [flush]);

  const requestProgress = (percent: number) => {
    if (percent <= lastAcknowledged.current) return;
    const previousPending = pending.current;
    if (previousPending === null || (percent === 100 && previousPending < 100)) {
      automaticRetries.current = 0;
      retriesExhausted.current = false;
    }
    pending.current = Math.max(previousPending ?? 0, percent);
    if (percent === 100 && retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    flushRef.current();
  };

  const report = (video: HTMLVideoElement, force: boolean) => {
    if (!Number.isFinite(video.duration) || video.duration <= 0 || !Number.isFinite(video.currentTime)) return;
    const percent = Math.min(99, Math.max(1, Math.floor((video.currentTime / video.duration) * 100)));
    const reportedBaseline = Math.max(lastAcknowledged.current, pending.current ?? 0);
    if (percent <= reportedBaseline || (!force && percent - reportedBaseline < 10)) return;
    requestProgress(percent);
  };

  return <video
    className="protected-video"
    controls
    controlsList="nodownload"
    playsInline
    preload="metadata"
    src={asset.view_href}
    aria-label={asset.title}
    onTimeUpdate={(event) => report(event.currentTarget, false)}
    onPause={(event) => report(event.currentTarget, true)}
    onEnded={() => requestProgress(100)}
  >Seu navegador não consegue reproduzir este vídeo.</video>;
}

export function isLearningPlayerResponse(value: unknown): value is {
  player: Omit<LearningPlayerPayload, "csrf">;
  csrf: LearningPlayerPayload["csrf"];
} {
  if (!isRecord(value) || !isStringRecord(value.csrf, ["progress", "complete"]) || !isRecord(value.player)) return false;
  return isLearningPlayerPayload({ ...value.player, csrf: value.csrf });
}

export function isProgressMutation(value: unknown): value is ProgressMutation {
  return isRecord(value) && isStringRecord(value, ["entitlement_id", "enrollment_id", "lesson_id", "resume_lesson_id"]) &&
    isPercent(value.lesson_progress_percent) && isPercent(value.course_progress_percent) &&
    isNonNegativeInteger(value.state_version) && typeof value.applied === "boolean";
}

export function isProgressMutationResponse(value: unknown): value is { progress: ProgressMutation } {
  return isRecord(value) && isProgressMutation(value.progress);
}

function isCourse(value: unknown): value is LearningPlayerPayload["course"] {
  if (!isStringRecord(value, ["id", "title"]) || typeof value.description !== "string" || !isNonNegativeInteger(value.position) || !Array.isArray(value.modules)) return false;
  return value.modules.length > 0 && value.modules.every((module) => isStringRecord(module, ["id", "title"]) &&
    module.status === "active" && isNonNegativeInteger(module.position) && Array.isArray(module.lessons) &&
    module.lessons.every((lesson) => isStringRecord(lesson, ["id", "title"]) && typeof lesson.content === "string" &&
      lesson.kind === "text" && lesson.status === "active" && isNonNegativeInteger(lesson.position)));
}

function isLessonProgress(value: unknown): value is LessonProgress {
  return isStringRecord(value, ["lesson_id"]) && (value.updated_at === null || isNonEmptyString(value.updated_at)) && isPercent(value.progress_percent) &&
    typeof value.completed === "boolean" && value.completed === (value.progress_percent === 100);
}

function isStringRecord(value: unknown, keys: string[]): value is Record<string, unknown> {
  return isRecord(value) && keys.every((key) => isNonEmptyString(value[key]));
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isPercent(value: unknown): value is number {
  return isNonNegativeInteger(value) && value <= 100;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isInteger(value) && typeof value === "number" && value > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && typeof value === "number" && value >= 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
