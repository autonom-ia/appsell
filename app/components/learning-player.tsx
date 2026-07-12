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
  selected_asset?: { href: string; title: string; media_type: "application/pdf" } | null;
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
const TENANT_ASSET_HREF = /^\/api\/library\/items\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/assets\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/;

export function LearningPlayer({ entitlementId, lessonId }: { entitlementId: string; lessonId?: string }) {
  const router = useRouter();
  const desiredKey = `${entitlementId}:${lessonId ?? "resume"}`;
  const [state, setState] = useState<PlayerState>({ status: "loading", requestKey: desiredKey });
  const [mutationState, setMutationState] = useState<"idle" | "opening" | "completing">("idle");
  const [feedback, setFeedback] = useState("");
  const requestRef = useRef<AbortController | null>(null);
  const mutationRef = useRef<AbortController | null>(null);
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
      if (mutationRef.current) return;
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
    requestRef.current?.abort();
    mutationRef.current?.abort();
    setFeedback("");
    setState({ status: "loading", requestKey: `${entitlementId}:${targetLessonId}` });
  }

  async function saveProgress(current: LearningPlayerPayload, currentLessonId: string, percent: number) {
    if (mutationState !== "idle") return;
    requestRef.current?.abort();
    const operation = `progress:${currentLessonId}:${percent}`;
    const controller = new AbortController();
    mutationRef.current?.abort();
    mutationRef.current = controller;
    setMutationState("opening");
    setFeedback("");
    const form = new FormData();
    form.set("_csrf", current.csrf.progress);
    form.set("idempotency_key", idempotencyKey(idempotencyKeys.current, operation));
    form.set("progress_percent", String(percent));
    form.set("expected_version", String(current.state_version));
    await mutate(`${lessonEndpoint(entitlementId, currentLessonId)}/progress`, "PUT", form, controller, currentLessonId, current.enrollment_id, false);
  }

  async function completeLesson() {
    if (!payload || !selected || mutationState !== "idle") return;
    requestRef.current?.abort();
    const currentLessonId = selected.lesson.id;
    const controller = new AbortController();
    mutationRef.current?.abort();
    mutationRef.current = controller;
    setMutationState("completing");
    setFeedback("");
    const form = new FormData();
    form.set("_csrf", payload.csrf.complete);
    form.set("idempotency_key", idempotencyKey(idempotencyKeys.current, `complete:${currentLessonId}`));
    form.set("expected_version", String(payload.state_version));
    await mutate(`${lessonEndpoint(entitlementId, currentLessonId)}/complete`, "POST", form, controller, currentLessonId, payload.enrollment_id, true);
  }

  async function mutate(endpoint: string, method: "PUT" | "POST", form: FormData, controller: AbortController, currentLessonId: string, expectedEnrollmentId: string, complete: boolean) {
    try {
      const response = await fetch(endpoint, { method, body: form, cache: "no-store", signal: controller.signal });
      if (controller.signal.aborted) return;
      if (response.status === 401) return setState({ status: "unauthorized", requestKey: desiredKey });
      if (response.status === 403 || response.status === 404) return setState({ status: "forbidden", requestKey: desiredKey });
      if (response.status === 409) return setState({ status: "conflict", requestKey: desiredKey });
      if (!response.ok) {
        setFeedback("Seu progresso não foi salvo. Tente novamente.");
        return;
      }
      const responsePayload: unknown = await response.json();
      if (!isProgressMutationResponse(responsePayload) || responsePayload.progress.entitlement_id !== entitlementId ||
        responsePayload.progress.enrollment_id !== expectedEnrollmentId || responsePayload.progress.lesson_id !== currentLessonId) {
        setFeedback("A resposta de progresso não pôde ser validada.");
        return;
      }
      const result = responsePayload.progress;
      setState((currentState) => {
        if (currentState.status !== "ready" || currentState.payload.selected_lesson_id !== currentLessonId) return currentState;
        return {
          ...currentState,
          payload: applyProgressMutation(currentState.payload, result)
        };
      });
      idempotencyKeys.current.delete(`${complete ? "complete" : "progress"}:${currentLessonId}${complete ? "" : ":1"}`);
      if (complete && nextId) {
        setState({ status: "loading", requestKey: `${entitlementId}:${nextId}` });
        router.push(lessonHref(entitlementId, nextId));
      } else {
        setFeedback(complete ? "Curso concluído. Seu progresso foi salvo." : "Progresso salvo.");
      }
    } catch {
      if (!controller.signal.aborted) setFeedback("Seu progresso não foi salvo. Verifique a conexão e tente novamente.");
    } finally {
      if (mutationRef.current === controller) mutationRef.current = null;
      setMutationState("idle");
    }
  }

  useEffect(() => {
    if (!payload || !selected || selectedProgress?.completed || (selectedProgress?.progress_percent ?? 0) > 0) return;
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
        {payload.selected_asset ? <aside className="lesson-material" aria-labelledby="lesson-material-title">
          <div><p className="eyebrow">Material complementar</p><h2 id="lesson-material-title">{payload.selected_asset.title}</h2><p>PDF protegido, aberto em uma nova aba. O texto principal da aula permanece disponível nesta página.</p></div>
          <a className="secondary-button" href={payload.selected_asset.href} target="_blank" rel="noopener">Abrir PDF<span className="sr-only"> em nova aba</span></a>
        </aside> : <p className="asset-unavailable" role="status">Material complementar indisponível para esta aula.</p>}
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
    !isStringRecord(value.csrf, ["progress", "complete"])) return false;
  if (value.selected_asset !== undefined && value.selected_asset !== null) {
    if (!isStringRecord(value.selected_asset, ["href", "title"]) || value.selected_asset.media_type !== "application/pdf") return false;
    const assetHref = (value.selected_asset.href as string).match(TENANT_ASSET_HREF);
    if (!assetHref || assetHref[1] !== value.entitlement_id) return false;
  }
  const lessonIds = flattenLessons(value.course.modules).map((entry) => entry.lesson.id);
  return lessonIds.length > 0 && new Set(lessonIds).size === lessonIds.length &&
    lessonIds.includes(value.selected_lesson_id) && lessonIds.includes(value.resume_lesson_id) &&
    value.lesson_progress.every((entry) => lessonIds.includes(entry.lesson_id)) &&
    new Set(value.lesson_progress.map((entry) => entry.lesson_id)).size === value.lesson_progress.length;
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
