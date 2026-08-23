"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { LessonAssetPanel } from "./lesson-asset-panel";

interface CurriculumLesson {
  id: string;
  title: string;
  kind: "text";
  content: string;
  status: string;
  position: number;
}

interface CurriculumModule {
  id: string;
  title: string;
  status: string;
  position: number;
  lessons: CurriculumLesson[];
}

interface CurriculumPayload {
  course: { id: string; draft_version: number };
  modules: CurriculumModule[];
  csrf: { create: string; update: string; reorder: string; archive: string };
  limits: {
    modules_per_course: number;
    lessons_per_module: number;
    lessons_per_course: number;
    title_bytes: number;
    lesson_content_bytes: number;
  };
  usage?: {
    modules_total: number;
    lessons_total: number;
    lessons_by_module: Record<string, number>;
  };
}

type LoadState = "loading" | "ready" | "unauthorized" | "forbidden" | "not_found" | "unavailable" | "error";
type MutationState = "idle" | "saving" | "conflict" | "invalid" | "forbidden" | "unavailable" | "error";

const defaultLimits = {
  modules_per_course: 50,
  lessons_per_module: 100,
  lessons_per_course: 500,
  title_bytes: 160,
  lesson_content_bytes: 8_192
};

export function CurriculumWorkspace({
  sellerId,
  productId,
  courseId,
  courseTitle,
  archived,
  onDirtyChange,
  onBusyChange,
  onSummaryChange,
  onCommitted
}: {
  sellerId: string;
  productId: string;
  courseId: string;
  courseTitle: string;
  archived: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onSummaryChange: (summary: { modules: number; lessons: number }) => void;
  onCommitted: () => void;
}) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [modules, setModules] = useState<CurriculumModule[]>([]);
  const [courseVersion, setCourseVersion] = useState(1);
  const [csrf, setCsrf] = useState<CurriculumPayload["csrf"]>({ create: "", update: "", reorder: "", archive: "" });
  const [limits, setLimits] = useState(defaultLimits);
  const [usage, setUsage] = useState({
    modules_total: 0,
    lessons_total: 0,
    lessons_by_module: {} as Record<string, number>
  });
  const [moduleTitle, setModuleTitle] = useState("");
  const [lessonDrafts, setLessonDrafts] = useState<Record<string, { title: string; content: string }>>({});
  const [editDrafts, setEditDrafts] = useState<Record<string, string>>({});
  const [mutationState, setMutationState] = useState<MutationState>("idle");
  const [message, setMessage] = useState("");
  const [invalidField, setInvalidField] = useState("");
  const [assetActivity, setAssetActivity] = useState<Record<string, { dirty: boolean; busy: boolean }>>({});
  const mutationKeys = useRef(new Map<string, string>());

  const assetDirty = Object.values(assetActivity).some((activity) => activity.dirty || activity.busy);
  const assetBusy = Object.values(assetActivity).some((activity) => activity.busy);
  const dirty = moduleTitle.length > 0 || Object.values(lessonDrafts).some((draft) => draft.title.length > 0 || draft.content.length > 0) || Object.keys(editDrafts).length > 0 || assetDirty;

  const handleAssetActivity = useCallback((lessonId: string, value: { dirty: boolean; busy: boolean }) => {
    setAssetActivity((current) => {
      const previous = current[lessonId];
      if (previous?.dirty === value.dirty && previous.busy === value.busy) return current;
      if (!value.dirty && !value.busy) {
        const next = { ...current };
        delete next[lessonId];
        return next;
      }
      return { ...current, [lessonId]: value };
    });
  }, []);

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => onBusyChange(assetBusy), [assetBusy, onBusyChange]);
  useEffect(() => () => onBusyChange(false), [onBusyChange]);
  useEffect(() => onSummaryChange({ modules: modules.length, lessons: modules.reduce((total, module) => total + module.lessons.length, 0) }), [modules, onSummaryChange]);

  const endpoint = `/api/sellers/${sellerId}/products/${productId}/courses/${courseId}`;
  const load = useCallback(async () => {
    setLoadState("loading");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(`${endpoint}/curriculum`, { cache: "no-store", signal: controller.signal });
      if (response.status === 401) return setLoadState("unauthorized");
      if (response.status === 403) return setLoadState("forbidden");
      if (response.status === 404) return setLoadState("not_found");
      if (response.status === 503) return setLoadState("unavailable");
      if (!response.ok) return setLoadState("error");
      const payload = await response.json() as CurriculumPayload;
      if (!validPayload(payload, courseId)) return setLoadState("error");
      setModules(payload.modules);
      setCourseVersion(payload.course.draft_version);
      setCsrf(payload.csrf);
      setLimits(payload.limits ?? defaultLimits);
      setUsage(payload.usage ?? {
        modules_total: payload.modules.length,
        lessons_total: payload.modules.reduce((total, module) => total + module.lessons.length, 0),
        lessons_by_module: Object.fromEntries(payload.modules.map((module) => [module.id, module.lessons.length]))
      });
      setMutationState("idle");
      setLoadState("ready");
    } catch {
      setLoadState("unavailable");
    } finally {
      window.clearTimeout(timeout);
    }
  }, [courseId, endpoint]);

  const handleAssetCommitted = useCallback(() => {
    onCommitted();
    void load();
  }, [load, onCommitted]);

  useEffect(() => {
    const start = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(start);
  }, [load]);

  async function mutate(
    operation: string,
    url: string,
    method: "POST" | "PUT" | "PATCH",
    action: keyof CurriculumPayload["csrf"],
    fields: Record<string, string>,
    onSuccess: (payload: Record<string, unknown>) => void,
    submittedForm?: HTMLFormElement
  ) {
    if (mutationState === "saving" || archived || assetDirty) return;
    const key = mutationKeys.current.get(operation) ?? crypto.randomUUID();
    mutationKeys.current.set(operation, key);
    const form = new FormData();
    form.set("_csrf", csrf[action]);
    form.set("idempotency_key", key);
    form.set("expected_course_version", String(courseVersion));
    for (const [name, value] of Object.entries(fields)) form.set(name, value);
    setMutationState("saving");
    setMessage("");
    setInvalidField("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await fetch(url, { method, body: form, signal: controller.signal });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) setMutationState("forbidden");
        else if (response.status === 409) {
          mutationKeys.current.delete(operation);
          setMutationState("conflict");
        } else if (response.status === 422) {
          const payload = await response.json().catch(() => null) as { field?: string } | null;
          const field = payload?.field ?? "form";
          setInvalidField(`${operation}:${field}`);
          setMutationState("invalid");
          const input = submittedForm?.elements.namedItem(field);
          if (input instanceof HTMLElement) queueMicrotask(() => input.focus());
        } else if (response.status === 503) setMutationState("unavailable");
        else setMutationState("error");
        return;
      }
      const payload = await response.json() as Record<string, unknown>;
      if (typeof payload.course_version !== "number") return setMutationState("error");
      mutationKeys.current.delete(operation);
      setCourseVersion(payload.course_version);
      onSuccess(payload);
      onCommitted();
      setMutationState("idle");
    } catch {
      setMutationState("unavailable");
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function createModule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await mutate("module:create", `${endpoint}/modules`, "POST", "create", { title: moduleTitle }, () => {
      setModuleTitle("");
      setMessage(`Módulo ${moduleTitle} criado.`);
      void load();
    }, event.currentTarget);
  }

  async function createLesson(event: FormEvent<HTMLFormElement>, module: CurriculumModule) {
    event.preventDefault();
    const draft = lessonDrafts[module.id] ?? { title: "", content: "" };
    await mutate(`lesson:create:${module.id}`, `${endpoint}/modules/${module.id}/lessons`, "POST", "create", {
      title: draft.title,
      kind: "text",
      content: draft.content
    }, () => {
      setLessonDrafts((current) => ({ ...current, [module.id]: { title: "", content: "" } }));
      setMessage(`Aula ${draft.title} criada em ${module.title}.`);
      void load();
    }, event.currentTarget);
  }

  async function updateModule(event: FormEvent<HTMLFormElement>, module: CurriculumModule) {
    event.preventDefault();
    const title = editDrafts[`module:${module.id}`] ?? module.title;
    await mutate(`module:update:${module.id}`, `${endpoint}/modules/${module.id}`, "PATCH", "update", { title }, () => {
      setEditDrafts((current) => removeKey(current, `module:${module.id}`));
      setMessage(`Módulo atualizado para ${title}.`);
      void load();
    }, event.currentTarget);
  }

  async function updateLesson(event: FormEvent<HTMLFormElement>, module: CurriculumModule, lesson: CurriculumLesson) {
    event.preventDefault();
    const title = editDrafts[`lesson-title:${lesson.id}`] ?? lesson.title;
    const content = editDrafts[`lesson-content:${lesson.id}`] ?? lesson.content;
    await mutate(`lesson:update:${lesson.id}`, `${endpoint}/modules/${module.id}/lessons/${lesson.id}`, "PATCH", "update", {
      title,
      kind: "text",
      content
    }, () => {
      setEditDrafts((current) => removeKeys(current, [`lesson-title:${lesson.id}`, `lesson-content:${lesson.id}`]));
      setMessage(`Aula ${title} atualizada.`);
      void load();
    }, event.currentTarget);
  }

  async function reorderModules(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= modules.length) return;
    const reordered = swap(modules, index, target);
    const moved = modules[index];
    await mutate(`modules:order:${moved.id}:${target}`, `${endpoint}/modules/order`, "PUT", "reorder", {
      ordered_ids: JSON.stringify(reordered.map((module) => module.id))
    }, () => {
      setModules(reordered.map((module, position) => ({ ...module, position })));
      setMessage(`Módulo ${moved.title} movido para a posição ${target + 1} de ${modules.length}.`);
      const focusDirection = target === 0 ? "down" : target === modules.length - 1 ? "up" : direction === -1 ? "up" : "down";
      focusAfterMutation(`module-move-${focusDirection}-${moved.id}`);
    });
  }

  async function reorderLessons(module: CurriculumModule, index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= module.lessons.length) return;
    const reordered = swap(module.lessons, index, target);
    const moved = module.lessons[index];
    await mutate(`lessons:order:${module.id}:${moved.id}:${target}`, `${endpoint}/modules/${module.id}/lessons/order`, "PUT", "reorder", {
      ordered_ids: JSON.stringify(reordered.map((lesson) => lesson.id))
    }, () => {
      setModules((current) => current.map((entry) => entry.id === module.id
        ? { ...entry, lessons: reordered.map((lesson, position) => ({ ...lesson, position })) }
        : entry));
      setMessage(`Aula ${moved.title} movida para a posição ${target + 1} de ${module.lessons.length}.`);
      const focusDirection = target === 0 ? "down" : target === module.lessons.length - 1 ? "up" : direction === -1 ? "up" : "down";
      focusAfterMutation(`lesson-move-${focusDirection}-${moved.id}`);
    });
  }

  async function archiveModule(module: CurriculumModule) {
    if (!window.confirm(`Arquivar o módulo ${module.title} e suas aulas?`)) return;
    await mutate(`module:archive:${module.id}`, `${endpoint}/modules/${module.id}/archive`, "POST", "archive", {}, () => {
      setEditDrafts((current) => removeKeys(current, [
        `module:${module.id}`,
        ...module.lessons.flatMap((lesson) => [`lesson-title:${lesson.id}`, `lesson-content:${lesson.id}`])
      ]));
      setLessonDrafts((current) => removeKey(current, module.id));
      setMessage(`Módulo ${module.title} arquivado.`);
      void load();
    });
  }

  async function archiveLesson(module: CurriculumModule, lesson: CurriculumLesson) {
    if (!window.confirm(`Arquivar a aula ${lesson.title}?`)) return;
    await mutate(`lesson:archive:${lesson.id}`, `${endpoint}/modules/${module.id}/lessons/${lesson.id}/archive`, "POST", "archive", {}, () => {
      setEditDrafts((current) => removeKeys(current, [`lesson-title:${lesson.id}`, `lesson-content:${lesson.id}`]));
      setMessage(`Aula ${lesson.title} arquivada.`);
      void load();
    });
  }

  if (loadState !== "ready") {
    const labels: Record<Exclude<LoadState, "ready">, string> = {
      loading: "Carregando currículo…",
      unauthorized: "Sua sessão expirou. Entre novamente.",
      forbidden: "Você não pode acessar o currículo deste espaço.",
      not_found: "Curso não encontrado neste espaço.",
      unavailable: "A API do currículo não respondeu. Nenhum conteúdo demonstrativo foi usado.",
      error: "Não foi possível carregar o currículo."
    };
    return <section className="curriculum-state" aria-live="polite"><h2>{labels[loadState]}</h2>{loadState === "unavailable" || loadState === "error" ? <button className="primary-button" type="button" onClick={() => void load()}>Tentar novamente</button> : null}</section>;
  }

  return (
    <section className="curriculum-workspace" aria-labelledby="curriculum-title" data-testid="curriculum-workspace">
      <header className="curriculum-header">
        <div><p className="eyebrow">Estrutura do curso</p><h2 id="curriculum-title">Módulos e aulas</h2><p><strong>{courseTitle}</strong> · versão {courseVersion}</p></div>
        <span>{usage.modules_total}/{limits.modules_per_course} módulos usados</span>
      </header>
      <p className="curriculum-intro">Organize aulas de texto e PDFs verificados em uma sequência clara. Vídeos, checkout e acesso pago ainda não estão disponíveis.</p>
      {archived ? <p className="form-note" role="status">Produto arquivado: currículo disponível somente para leitura.</p> : null}
      {assetDirty ? <p className="form-note" role="status">Conclua ou cancele o envio do PDF antes de alterar a estrutura do currículo.</p> : null}
      <div className={`curriculum-feedback state-${mutationState}`} role="status" aria-live="polite" aria-atomic="true" data-testid="curriculum-live">
        {mutationState === "idle" ? message || mutationMessage(mutationState) : mutationMessage(mutationState)}
        {mutationState === "conflict" ? <button className="secondary-button" type="button" onClick={() => void load()}>Recarregar currículo</button> : null}
      </div>

      {!archived ? <form className="curriculum-create" onSubmit={createModule} data-testid="module-create-form">
        <label htmlFor="new-module-title">Novo módulo</label>
        <input id="new-module-title" name="title" value={moduleTitle} onChange={(event) => { setModuleTitle(event.target.value); setMutationState("idle"); }} minLength={2} maxLength={160} required aria-invalid={invalidField === "module:create:title"} aria-describedby={invalidField === "module:create:title" ? "new-module-title-error" : undefined}/>
        {invalidField === "module:create:title" ? <span id="new-module-title-error" className="form-error">Informe um nome de módulo válido.</span> : null}
        <button className="primary-button" type="submit" disabled={mutationState === "saving" || assetDirty || usage.modules_total >= limits.modules_per_course}>{mutationState === "saving" ? "Salvando…" : "Adicionar módulo"}</button>
      </form> : null}

      {modules.length === 0 ? <div className="curriculum-empty" data-testid="curriculum-empty"><strong>Seu curso ainda não tem módulos.</strong><p>Adicione o primeiro módulo para começar a organizar a jornada do aluno.</p></div> : (
        <ol className="curriculum-modules">
          {modules.map((module, moduleIndex) => <li key={module.id} data-testid={`module-${module.id}`}>
            <article className="curriculum-module" aria-labelledby={`module-title-${module.id}`}>
              <header><div><span>Módulo {moduleIndex + 1}</span><h3 id={`module-title-${module.id}`}>{module.title}</h3><small>{module.lessons.length}/{limits.lessons_per_module} aulas</small></div><div className="order-actions" aria-label={`Ordenar módulo ${module.title}`}><button data-focus-key={`module-move-up-${module.id}`} type="button" className="secondary-button" disabled={archived || moduleIndex === 0 || mutationState === "saving" || assetDirty} onClick={() => void reorderModules(moduleIndex, -1)} aria-label={`Mover módulo ${module.title} para cima`}>↑ <span>Subir</span></button><button data-focus-key={`module-move-down-${module.id}`} type="button" className="secondary-button" disabled={archived || moduleIndex === modules.length - 1 || mutationState === "saving" || assetDirty} onClick={() => void reorderModules(moduleIndex, 1)} aria-label={`Mover módulo ${module.title} para baixo`}>↓ <span>Descer</span></button></div></header>
              {!archived ? <details className="curriculum-edit"><summary>Editar módulo</summary><form onSubmit={(event) => void updateModule(event, module)}><label htmlFor={`module-title-input-${module.id}`}>Nome do módulo</label><input id={`module-title-input-${module.id}`} name="title" value={editDrafts[`module:${module.id}`] ?? module.title} onChange={(event) => setEditDrafts((current) => ({ ...current, [`module:${module.id}`]: event.target.value }))} minLength={2} maxLength={160} required aria-invalid={invalidField === `module:update:${module.id}:title`} aria-describedby={invalidField === `module:update:${module.id}:title` ? `module-title-error-${module.id}` : undefined}/>{invalidField === `module:update:${module.id}:title` ? <span id={`module-title-error-${module.id}`} className="form-error">Informe um nome de módulo válido.</span> : null}<div className="inline-actions"><button className="primary-button" type="submit" disabled={mutationState === "saving" || assetDirty}>Salvar módulo</button><button className="danger-button" type="button" disabled={assetDirty} onClick={() => void archiveModule(module)}>Arquivar módulo</button></div></form></details> : null}
              {module.lessons.length === 0 ? <p className="lesson-empty">Nenhuma aula neste módulo.</p> : <ol className="curriculum-lessons">{module.lessons.map((lesson, lessonIndex) => <li key={lesson.id} data-testid={`lesson-${lesson.id}`}><article aria-labelledby={`lesson-title-${lesson.id}`}><header><div><span>Aula {lessonIndex + 1}</span><h4 id={`lesson-title-${lesson.id}`}>{lesson.title}</h4></div><div className="order-actions"><button data-focus-key={`lesson-move-up-${lesson.id}`} className="secondary-button" type="button" disabled={archived || lessonIndex === 0 || mutationState === "saving" || assetDirty} onClick={() => void reorderLessons(module, lessonIndex, -1)} aria-label={`Mover aula ${lesson.title} para cima`}>↑ <span>Subir</span></button><button data-focus-key={`lesson-move-down-${lesson.id}`} className="secondary-button" type="button" disabled={archived || lessonIndex === module.lessons.length - 1 || mutationState === "saving" || assetDirty} onClick={() => void reorderLessons(module, lessonIndex, 1)} aria-label={`Mover aula ${lesson.title} para baixo`}>↓ <span>Descer</span></button></div></header><p>{lesson.content || "Aula de texto ainda sem conteúdo."}</p><LessonAssetPanel sellerId={sellerId} productId={productId} courseId={courseId} moduleId={module.id} lessonId={lesson.id} lessonTitle={lesson.title} courseVersion={courseVersion} archived={archived} onActivityChange={handleAssetActivity} onCommitted={handleAssetCommitted}/>{!archived ? <details className="curriculum-edit"><summary>Editar aula</summary><form onSubmit={(event) => void updateLesson(event, module, lesson)}><label htmlFor={`lesson-title-input-${lesson.id}`}>Nome da aula</label><input id={`lesson-title-input-${lesson.id}`} name="title" value={editDrafts[`lesson-title:${lesson.id}`] ?? lesson.title} onChange={(event) => setEditDrafts((current) => ({ ...current, [`lesson-title:${lesson.id}`]: event.target.value }))} minLength={2} maxLength={160} required aria-invalid={invalidField === `lesson:update:${lesson.id}:title`} aria-describedby={invalidField === `lesson:update:${lesson.id}:title` ? `lesson-title-error-${lesson.id}` : undefined}/>{invalidField === `lesson:update:${lesson.id}:title` ? <span id={`lesson-title-error-${lesson.id}`} className="form-error">Informe um nome de aula válido.</span> : null}<label htmlFor={`lesson-content-input-${lesson.id}`}>Conteúdo textual</label><textarea id={`lesson-content-input-${lesson.id}`} name="content" value={editDrafts[`lesson-content:${lesson.id}`] ?? lesson.content} onChange={(event) => setEditDrafts((current) => ({ ...current, [`lesson-content:${lesson.id}`]: event.target.value }))} maxLength={8192} aria-invalid={invalidField === `lesson:update:${lesson.id}:content`} aria-describedby={invalidField === `lesson:update:${lesson.id}:content` ? `lesson-content-error-${lesson.id}` : undefined}/>{invalidField === `lesson:update:${lesson.id}:content` ? <span id={`lesson-content-error-${lesson.id}`} className="form-error">O conteúdo textual ultrapassa o limite permitido.</span> : null}<div className="inline-actions"><button className="primary-button" type="submit" disabled={mutationState === "saving" || assetDirty}>Salvar aula</button><button className="danger-button" type="button" disabled={assetDirty} onClick={() => void archiveLesson(module, lesson)}>Arquivar aula</button></div></form></details> : null}</article></li>)}</ol>}
              {!archived ? <form className="lesson-create" onSubmit={(event) => void createLesson(event, module)} data-testid={`lesson-create-form-${module.id}`}><fieldset disabled={mutationState === "saving" || assetDirty || (usage.lessons_by_module[module.id] ?? module.lessons.length) >= limits.lessons_per_module || usage.lessons_total >= limits.lessons_per_course}><legend>Adicionar aula de texto</legend><label htmlFor={`new-lesson-title-${module.id}`}>Nome da aula</label><input id={`new-lesson-title-${module.id}`} name="title" value={lessonDrafts[module.id]?.title ?? ""} onChange={(event) => setLessonDrafts((current) => ({ ...current, [module.id]: { title: event.target.value, content: current[module.id]?.content ?? "" } }))} minLength={2} maxLength={160} required aria-invalid={invalidField === `lesson:create:${module.id}:title`} aria-describedby={invalidField === `lesson:create:${module.id}:title` ? `new-lesson-title-error-${module.id}` : undefined}/>{invalidField === `lesson:create:${module.id}:title` ? <span id={`new-lesson-title-error-${module.id}`} className="form-error">Informe um nome de aula válido.</span> : null}<label htmlFor={`new-lesson-content-${module.id}`}>Conteúdo textual</label><textarea id={`new-lesson-content-${module.id}`} name="content" value={lessonDrafts[module.id]?.content ?? ""} onChange={(event) => setLessonDrafts((current) => ({ ...current, [module.id]: { title: current[module.id]?.title ?? "", content: event.target.value } }))} maxLength={8192} aria-invalid={invalidField === `lesson:create:${module.id}:content`} aria-describedby={invalidField === `lesson:create:${module.id}:content` ? `new-lesson-content-error-${module.id}` : undefined}/>{invalidField === `lesson:create:${module.id}:content` ? <span id={`new-lesson-content-error-${module.id}`} className="form-error">O conteúdo textual ultrapassa o limite permitido.</span> : null}<button className="primary-button" type="submit">Adicionar aula</button></fieldset></form> : null}
            </article>
          </li>)}
        </ol>
      )}
    </section>
  );
}

function validPayload(payload: CurriculumPayload, expectedCourseId: string) {
  return payload && payload.course?.id === expectedCourseId && Number.isSafeInteger(payload.course.draft_version) && Array.isArray(payload.modules) && payload.csrf && typeof payload.csrf.create === "string";
}

function mutationMessage(state: MutationState) {
  const messages: Record<MutationState, string> = {
    idle: "Currículo sincronizado.",
    saving: "Salvando currículo…",
    conflict: "O currículo mudou no servidor. Recarregue antes de tentar novamente.",
    invalid: "Revise o campo indicado e tente novamente.",
    forbidden: "Sua sessão ou permissão neste espaço mudou.",
    unavailable: "A API não respondeu. Seu texto e a chave da tentativa foram preservados.",
    error: "Não foi possível salvar o currículo."
  };
  return messages[state];
}

function swap<T>(items: T[], first: number, second: number) {
  const copy = [...items];
  [copy[first], copy[second]] = [copy[second], copy[first]];
  return copy;
}

function focusAfterMutation(key: string) {
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
    document.querySelector<HTMLElement>(`[data-focus-key="${key}"]`)?.focus();
  }));
}

function removeKey<T>(value: Record<string, T>, key: string) {
  const next = { ...value };
  delete next[key];
  return next;
}

function removeKeys(value: Record<string, string>, keys: string[]) {
  const next = { ...value };
  for (const key of keys) delete next[key];
  return next;
}
