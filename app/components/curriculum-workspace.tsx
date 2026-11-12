"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createSerializer,
  DEFAULT_LIMITS,
  sendCurriculumMutation,
  validCurriculum,
  type CurriculumCsrf,
  type CurriculumLesson,
  type CurriculumLimits,
  type CurriculumModule,
  type MutationResult
} from "@/lib/curriculum/curriculum-api.client";
import { JOURNEY_COPY } from "@/lib/curriculum/journey-copy";
import { lessonReadiness, type LiveUpload } from "@/lib/curriculum/lesson-readiness";

import { CourseOutline } from "./course-outline";
import { LessonEditor } from "./lesson-editor";
import { UndoProvider, useScheduleUndo } from "./undo-toast";
import { useLessonSummaries } from "./use-lesson-summaries";

export interface AssetActivity {
  dirty: boolean;
  busy: boolean;
}

/**
 * Junta o que cada painel de anexos avisa. A chave é o painel, não a aula:
 * cada aula tem dois painéis (conteúdo e anexos), e com a chave da aula o
 * aviso de um sobrescrevia o do outro. Um painel que terminava apagava o
 * envio ainda em curso no irmão, e a tela se achava limpa (#612).
 */
export function mergeAssetActivity(
  current: Readonly<Record<string, AssetActivity>>,
  panelKey: string,
  value: AssetActivity
): Record<string, AssetActivity> {
  const previous = current[panelKey];
  if (previous?.dirty === value.dirty && previous.busy === value.busy) return current;
  if (!value.dirty && !value.busy) {
    const next = { ...current };
    delete next[panelKey];
    return next;
  }
  return { ...current, [panelKey]: value };
}

type LoadState = "loading" | "ready" | "unauthorized" | "forbidden" | "not_found" | "unavailable" | "error";

const TRANSPORT: readonly LiveUpload["phase"][] = ["hashing", "creating", "uploading", "confirming"];
/** Enquanto o arquivo ainda está sendo conferido, o editor fica montado,
 *  escondido: é ele quem acompanha a conferência e avisa pronto ou recusado. */
const KEEP_MOUNTED: readonly LiveUpload["phase"][] = [...TRANSPORT, "verifying", "delayed"];

export interface CurriculumWorkspaceProps {
  sellerId: string;
  productId: string;
  courseId: string;
  courseTitle: string;
  archived: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onSummaryChange: (summary: { modules: number; lessons: number }) => void;
  onCommitted: () => void;
}

/**
 * Montar o curso, do módulo à aula pronta (#649).
 *
 * Duas telas: a lista "Seu curso", com o estado de cada aula, e a aula aberta
 * (`?aula=<id>`), com vídeo ou PDF, texto e materiais. No celular uma de cada
 * vez; com espaço, lado a lado.
 */
export function CurriculumWorkspace(props: CurriculumWorkspaceProps) {
  return <UndoProvider><Workspace {...props}/></UndoProvider>;
}

function Workspace({
  sellerId,
  productId,
  courseId,
  archived,
  onDirtyChange,
  onBusyChange,
  onSummaryChange,
  onCommitted
}: CurriculumWorkspaceProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const scheduleUndo = useScheduleUndo();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [modules, setModules] = useState<CurriculumModule[]>([]);
  const [csrf, setCsrf] = useState<CurriculumCsrf>({ create: "", update: "", reorder: "", archive: "" });
  const [limits, setLimits] = useState<CurriculumLimits>(DEFAULT_LIMITS);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState("");
  const [structureBusy, setStructureBusy] = useState(false);
  const [assetActivity, setAssetActivity] = useState<Record<string, AssetActivity>>({});
  const [live, setLive] = useState<Record<string, LiveUpload>>({});
  const versionRef = useRef(1);
  const [courseVersion, setCourseVersion] = useState(1);
  const keys = useRef(new Map<string, string>());
  const serialize = useMemo(() => createSerializer(), []);
  // A aula que esta tela abriu com push: só ela volta com o "voltar" do
  // navegador. Aberta por endereço, ou depois de um voltar do navegador, a
  // saída troca o endereço em vez de sair da área do curso.
  const openedHere = useRef<string | null>(null);
  // O último nome e texto de cada aula, como a pessoa deixou. O pedido de
  // salvar é montado na hora em que sai da fila, com estes valores.
  const lessonDrafts = useRef(new Map<string, { title: string; content: string }>());
  const lastOpened = useRef<string | null>(null);
  const pendingFocus = useRef<string | null>(null);

  const endpoint = `/api/sellers/${sellerId}/products/${productId}/courses/${courseId}`;
  const selectedId = searchParams.get("aula");

  const visibleModules = useMemo(() => modules
    .filter((module) => !hidden.has(module.id))
    .map((module) => ({ ...module, lessons: module.lessons.filter((lesson) => !hidden.has(lesson.id)) })),
  [hidden, modules]);

  const lessonRefs = useMemo(() => visibleModules.flatMap((module) =>
    module.lessons.map((lesson) => ({ moduleId: module.id, lessonId: lesson.id }))), [visibleModules]);
  const { summaries, refresh: refreshSummary } = useLessonSummaries(endpoint, lessonRefs);

  const assetBusy = Object.values(assetActivity).some((activity) => activity.busy);
  const assetDirty = Object.values(assetActivity).some((activity) => activity.dirty || activity.busy);
  useEffect(() => onDirtyChange(assetDirty), [assetDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => onBusyChange(assetBusy), [assetBusy, onBusyChange]);
  useEffect(() => () => onBusyChange(false), [onBusyChange]);
  useEffect(() => onSummaryChange({
    modules: visibleModules.length,
    lessons: visibleModules.reduce((total, module) => total + module.lessons.length, 0)
  }), [onSummaryChange, visibleModules]);

  /**
   * Carregar e revalidar não são a mesma coisa: revalidar troca os dados por
   * baixo sem trocar a tela por "Carregando…", que desmontava tudo e levava o
   * scroll ao topo a cada clique.
   */
  const load = useCallback(async (mode: "initial" | "revalidate" = "initial") => {
    const initial = mode === "initial";
    if (initial) setLoadState("loading");
    const fail = (state: Exclude<LoadState, "ready">) => {
      const fatal = state === "unauthorized" || state === "forbidden" || state === "not_found";
      if (initial || fatal) return setLoadState(state);
      setNotice(JOURNEY_COPY.loadFailed);
    };
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(`${endpoint}/curriculum`, { cache: "no-store", signal: controller.signal });
      if (response.status === 401) return fail("unauthorized");
      if (response.status === 403) return fail("forbidden");
      if (response.status === 404) return fail("not_found");
      if (!response.ok) return fail(response.status === 503 ? "unavailable" : "error");
      const payload: unknown = await response.json();
      if (!validCurriculum(payload, courseId)) return fail("error");
      setModules(payload.modules);
      versionRef.current = payload.course.draft_version;
      setCourseVersion(payload.course.draft_version);
      setCsrf(payload.csrf);
      setLimits(payload.limits ?? DEFAULT_LIMITS);
      setLoadState("ready");
    } catch {
      fail("unavailable");
    } finally {
      window.clearTimeout(timeout);
    }
  }, [courseId, endpoint]);

  useEffect(() => {
    const start = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(start);
  }, [load]);

  /**
   * Uma mudança no curso, em fila, com a versão mais nova e chave de repetição.
   *
   * A chave vale para o mesmo pedido: operação e conteúdo. Repetir depois de
   * uma queda reenvia o mesmo pedido; mudar o conteúdo é outro pedido, com
   * outra chave, para o servidor não responder pelo conteúdo antigo.
   * `fields` pode ser uma função: o conteúdo é lido na hora em que o pedido
   * sai da fila, com o último valor. Ao sair da página (`keepalive`) o pedido
   * não espera a fila, senão não sairia antes de a aba fechar.
   */
  const mutate = useCallback((operation: string, url: string, method: "POST" | "PUT" | "PATCH",
    action: keyof CurriculumCsrf, fields: Record<string, string> | (() => Record<string, string>),
    options: { keepalive?: boolean } = {}
  ): Promise<MutationResult> => {
    const task = async () => {
      const body = typeof fields === "function" ? fields() : fields;
      const keyName = `${operation}\n${JSON.stringify(body)}`;
      const idempotencyKey = keys.current.get(keyName) ?? crypto.randomUUID();
      keys.current.set(keyName, idempotencyKey);
      const result = await sendCurriculumMutation({
        url, method, csrf: csrf[action], idempotencyKey, courseVersion: versionRef.current, fields: body,
        keepalive: options.keepalive
      });
      if (result.ok) {
        keys.current.delete(keyName);
        versionRef.current = result.courseVersion;
        setCourseVersion(result.courseVersion);
        onCommitted();
      } else if (result.failure !== "offline") {
        keys.current.delete(keyName);
      }
      return result;
    };
    return options.keepalive ? task() : serialize(task);
  }, [csrf, onCommitted, serialize]);

  const report = useCallback((result: MutationResult, invalidMessage: string = JOURNEY_COPY.nameTooShort) => {
    if (result.ok) return setNotice("");
    if (result.failure === "conflict") {
      setNotice(JOURNEY_COPY.changedElsewhere);
      void load("revalidate");
    } else if (result.failure === "forbidden") setNotice(JOURNEY_COPY.noPermission);
    else if (result.failure === "offline") setNotice(JOURNEY_COPY.offline);
    else if (result.failure === "invalid") setNotice(invalidMessage);
    else setNotice(JOURNEY_COPY.loadFailed);
  }, [load]);

  const structural = useCallback(async (task: () => Promise<MutationResult>, invalidMessage?: string) => {
    setStructureBusy(true);
    try {
      const result = await task();
      report(result, invalidMessage);
      return result;
    } finally {
      setStructureBusy(false);
    }
  }, [report]);

  const openLesson = useCallback((lessonId: string) => {
    lastOpened.current = lessonId;
    const href = `?section=curriculo&aula=${encodeURIComponent(lessonId)}`;
    if (selectedId) {
      // Trocar de aula com outra aberta (lado a lado) não empilha histórico:
      // o "voltar" continua levando para a lista, e não para a aula anterior.
      if (openedHere.current === selectedId) openedHere.current = lessonId;
      router.replace(href, { scroll: false });
      return;
    }
    openedHere.current = lessonId;
    router.push(href, { scroll: false });
  }, [router, selectedId]);

  /** Volta para a lista e devolve o foco à aula de onde a pessoa saiu. */
  const closeLesson = useCallback((focusSelector?: string) => {
    if (selectedId && openedHere.current === selectedId) {
      router.back();
    } else {
      router.replace("?section=curriculo", { scroll: false });
    }
    openedHere.current = null;
    const target = selectedId ?? lastOpened.current ?? "";
    // A troca de tela só acontece quando o roteador termina; o foco espera a
    // lista aparecer (o efeito abaixo), senão cairia num elemento escondido.
    pendingFocus.current = focusSelector ?? `[data-lesson-link="${cssText(target)}"]`;
  }, [router, selectedId]);

  useEffect(() => {
    if (selectedId || !pendingFocus.current) return;
    const selector = pendingFocus.current;
    pendingFocus.current = null;
    focusAfterRender(selector, "#curriculum-title");
  }, [selectedId]);

  const createModule = useCallback(async (title: string) => {
    const result = await structural(() => mutate("module:create", `${endpoint}/modules`, "POST", "create", { title }));
    if (!result.ok) return null;
    await load("revalidate");
    return typeof result.payload.module_id === "string" ? result.payload.module_id : null;
  }, [endpoint, load, mutate, structural]);

  const renameModule = useCallback(async (module: CurriculumModule, title: string) => {
    if (title === module.title) return true;
    const result = await structural(() => mutate(`module:update:${module.id}`, `${endpoint}/modules/${module.id}`, "PATCH", "update", { title }));
    if (result.ok) setModules((current) => current.map((entry) => entry.id === module.id ? { ...entry, title } : entry));
    return result.ok;
  }, [endpoint, mutate, structural]);

  const createLesson = useCallback(async (module: CurriculumModule, title: string) => {
    const result = await structural(() => mutate(`lesson:create:${module.id}`, `${endpoint}/modules/${module.id}/lessons`, "POST", "create", { title, kind: "text", content: "" }));
    if (!result.ok) return false;
    await load("revalidate");
    if (typeof result.payload.lesson_id === "string") openLesson(result.payload.lesson_id);
    return true;
  }, [endpoint, load, mutate, openLesson, structural]);

  const moveModule = useCallback(async (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= visibleModules.length) return;
    const reordered = swap(visibleModules, index, target);
    const moved = visibleModules[index];
    const result = await structural(() => mutate(`modules:order:${moved.id}:${target}`, `${endpoint}/modules/order`, "PUT", "reorder", {
      ordered_ids: JSON.stringify(reordered.map((module) => module.id))
    }), JOURNEY_COPY.orderFailed);
    if (!result.ok) return;
    setModules((current) => reorderLike(current, reordered.map((module) => module.id)));
    setNotice(`Módulo ${moved.title} agora é o ${target + 1}º de ${visibleModules.length}.`);
    focusAfterRender(`[data-move="module-${direction < 0 ? "up" : "down"}-${cssText(moved.id)}"]`, `[data-move="module-${direction < 0 ? "down" : "up"}-${cssText(moved.id)}"]`);
  }, [endpoint, mutate, structural, visibleModules]);

  const moveLesson = useCallback(async (module: CurriculumModule, index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= module.lessons.length) return;
    const reordered = swap(module.lessons, index, target);
    const moved = module.lessons[index];
    const result = await structural(() => mutate(`lessons:order:${module.id}:${moved.id}:${target}`, `${endpoint}/modules/${module.id}/lessons/order`, "PUT", "reorder", {
      ordered_ids: JSON.stringify(reordered.map((lesson) => lesson.id))
    }), JOURNEY_COPY.orderFailed);
    if (!result.ok) return;
    setModules((current) => current.map((entry) => entry.id === module.id
      ? { ...entry, lessons: reorderLike(entry.lessons, reordered.map((lesson) => lesson.id)) }
      : entry));
    setNotice(`Aula ${moved.title} agora é a ${target + 1}ª de ${module.lessons.length}.`);
    focusAfterRender(`[data-move="lesson-${direction < 0 ? "up" : "down"}-${cssText(moved.id)}"]`, `[data-move="lesson-${direction < 0 ? "down" : "up"}-${cssText(moved.id)}"]`);
  }, [endpoint, mutate, structural]);

  const hide = useCallback((id: string, value: boolean) => {
    setHidden((current) => {
      const next = new Set(current);
      if (value) next.add(id); else next.delete(id);
      return next;
    });
  }, []);

  const archiveCommit = useCallback((operation: string, url: string) => async ({ leaving }: { leaving: boolean }) => {
    const result = await mutate(operation, url, "POST", "archive", {}, { keepalive: leaving });
    if (!result.ok) {
      // O item volta para a lista; a versão do curso pode ter andado, então a
      // tela relê antes da próxima mudança.
      if (!leaving && result.failure !== "offline") void load("revalidate");
      throw new Error(result.failure);
    }
    if (!leaving) await load("revalidate");
  }, [load, mutate]);

  const deleteModule = useCallback((module: CurriculumModule) => {
    // Excluir o módulo desmontaria o editor de uma aula que ainda envia
    // arquivo, e o envio ficaria pela metade no servidor.
    if (module.lessons.some((lesson) => TRANSPORT.includes(live[lesson.id]?.phase ?? "idle"))) {
      setNotice(JOURNEY_COPY.deleteModuleBlocked);
      return;
    }
    // O foco não pode cair no vazio junto com o módulo: vai para o vizinho.
    const index = visibleModules.findIndex((entry) => entry.id === module.id);
    const neighbour = visibleModules[index + 1] ?? visibleModules[index - 1];
    const focusTarget = neighbour
      ? `[aria-label="Mudar nome do módulo ${cssText(neighbour.title)}"]`
      : "#new-module-title";
    if (module.lessons.some((lesson) => lesson.id === selectedId)) closeLesson(focusTarget);
    else focusAfterRender(focusTarget, "#curriculum-title");
    hide(module.id, true);
    scheduleUndo({
      id: `module:${module.id}`,
      name: module.title,
      message: `Módulo “${module.title}” excluído.`,
      commit: archiveCommit(`module:archive:${module.id}`, `${endpoint}/modules/${module.id}/archive`),
      revert: () => hide(module.id, false)
    });
  }, [archiveCommit, closeLesson, endpoint, hide, live, scheduleUndo, selectedId, visibleModules]);

  const deleteLesson = useCallback((module: CurriculumModule, lesson: CurriculumLesson) => {
    // A aula some da lista; o foco vai para a aula vizinha, ou para "+ Nova aula".
    const siblings = visibleModules.find((entry) => entry.id === module.id)?.lessons ?? [];
    const index = siblings.findIndex((entry) => entry.id === lesson.id);
    const neighbour = siblings[index + 1] ?? siblings[index - 1];
    const focusTarget = neighbour
      ? `[data-lesson-link="${cssText(neighbour.id)}"]`
      : `[aria-label="Nova aula em ${cssText(module.title)}"]`;
    if (selectedId === lesson.id) closeLesson(focusTarget);
    else focusAfterRender(focusTarget, "#curriculum-title");
    hide(lesson.id, true);
    scheduleUndo({
      id: `lesson:${lesson.id}`,
      name: lesson.title,
      message: `Aula “${lesson.title}” excluída.`,
      commit: archiveCommit(`lesson:archive:${lesson.id}`, `${endpoint}/modules/${module.id}/lessons/${lesson.id}/archive`),
      revert: () => hide(lesson.id, false)
    });
  }, [archiveCommit, closeLesson, endpoint, hide, scheduleUndo, selectedId, visibleModules]);

  /**
   * O salvar sozinho do nome e do texto da aula.
   *
   * O servidor pede nome e texto juntos. Os dois campos salvam cada um no seu
   * tempo, então o pedido leva o último valor de cada um, lido na hora em que
   * sai da fila: um nome salvo enquanto o texto ainda ia não pode mandar o
   * texto antigo de volta.
   */
  const saveLesson = useCallback(async (
    module: CurriculumModule,
    lesson: CurriculumLesson,
    change: { title?: string; content?: string },
    options: { keepalive?: boolean } = {}
  ) => {
    const known = modules.find((entry) => entry.id === module.id)?.lessons.find((entry) => entry.id === lesson.id) ?? lesson;
    const draft = { ...(lessonDrafts.current.get(lesson.id) ?? { title: known.title, content: known.content }), ...change };
    lessonDrafts.current.set(lesson.id, draft);
    let sent = draft;
    const fields = () => {
      sent = { ...(lessonDrafts.current.get(lesson.id) ?? draft) };
      return { title: sent.title, kind: "text", content: sent.content };
    };
    const send = () => mutate(`lesson:update:${lesson.id}`, `${endpoint}/modules/${module.id}/lessons/${lesson.id}`, "PATCH", "update", fields, options);
    let result = await send();
    // A versão do curso é uma só, e um arquivo que fica pronto também a muda.
    // Uma tentativa a mais com a versão nova resolve esse caso sem incomodar
    // ninguém; se o conflito voltar, quem decide é a pessoa.
    if (!result.ok && result.failure === "conflict" && !options.keepalive) {
      await load("revalidate");
      result = await send();
    }
    if (result.ok) {
      const latest = lessonDrafts.current.get(lesson.id);
      if (latest && latest.title === sent.title && latest.content === sent.content) lessonDrafts.current.delete(lesson.id);
      setModules((list) => list.map((entry) => entry.id !== module.id ? entry : {
        ...entry,
        lessons: entry.lessons.map((item) => item.id === lesson.id ? { ...item, title: sent.title, content: sent.content } : item)
      }));
      return { ok: true as const };
    }
    if (result.failure === "conflict") setNotice(JOURNEY_COPY.changedElsewhere);
    return { ok: false as const, reason: result.failure };
  }, [endpoint, load, modules, mutate]);

  const handleAssetActivity = useCallback((panelKey: string, value: AssetActivity) => {
    setAssetActivity((current) => mergeAssetActivity(current, panelKey, value));
  }, []);

  const handleLive = useCallback((lessonId: string, value: LiveUpload) => {
    setLive((current) => {
      const previous = current[lessonId];
      if (previous?.phase === value.phase && previous.progress === value.progress) return current;
      return { ...current, [lessonId]: value };
    });
  }, []);

  const handleAssetCommitted = useCallback((lessonId: string) => {
    onCommitted();
    refreshSummary(lessonId);
    void load("revalidate");
  }, [load, onCommitted, refreshSummary]);

  if (loadState !== "ready") {
    const labels: Record<Exclude<LoadState, "ready">, string> = {
      loading: JOURNEY_COPY.loading,
      unauthorized: JOURNEY_COPY.sessionExpired,
      forbidden: JOURNEY_COPY.noPermission,
      not_found: JOURNEY_COPY.notFound,
      unavailable: JOURNEY_COPY.loadFailed,
      error: JOURNEY_COPY.loadFailed
    };
    return <section className="curriculum-state" aria-live="polite">
      <h2>{labels[loadState]}</h2>
      {loadState === "unavailable" || loadState === "error"
        ? <button className="primary-button" type="button" onClick={() => void load()}>Tentar de novo</button>
        : null}
    </section>;
  }

  const readinessOf = (lesson: CurriculumLesson) => lessonReadiness({
    content: lesson.content,
    summary: summaries[lesson.id] ?? null,
    live: live[lesson.id] ?? null
  });

  const selected = visibleModules.flatMap((module) => module.lessons.map((lesson) => ({ module, lesson })))
    .find((entry) => entry.lesson.id === selectedId) ?? null;
  const mounted = visibleModules.flatMap((module) => module.lessons.map((lesson) => ({ module, lesson })))
    .filter((entry) => entry.lesson.id === selectedId || KEEP_MOUNTED.includes(live[entry.lesson.id]?.phase ?? "idle"));

  return (
    <section className="curriculum-workspace" data-view={selected ? "lesson" : "list"} aria-labelledby="curriculum-title" data-testid="curriculum-workspace">
      <div className="curriculum-split">
      <div className="curriculum-outline-pane">
        <CourseOutline
          modules={visibleModules}
          limits={limits}
          archived={archived}
          busy={structureBusy}
          selectedId={selected?.lesson.id ?? null}
          readinessOf={readinessOf}
          notice={notice}
          onOpenLesson={openLesson}
          onCreateModule={createModule}
          onRenameModule={renameModule}
          onDeleteModule={deleteModule}
          onMoveModule={moveModule}
          onCreateLesson={createLesson}
          onMoveLesson={moveLesson}
          onReload={() => void load()}
        />
      </div>
      <div className="curriculum-lesson-pane">
        {mounted.map(({ module, lesson }) => <LessonEditor
          key={lesson.id}
          hidden={lesson.id !== selected?.lesson.id}
          sellerId={sellerId}
          productId={productId}
          courseId={courseId}
          module={module}
          lesson={lesson}
          courseVersion={courseVersion}
          archived={archived}
          readiness={readinessOf(lesson)}
          transportBusy={TRANSPORT.includes(live[lesson.id]?.phase ?? "idle")}
          onBack={closeLesson}
          onDelete={() => deleteLesson(module, lesson)}
          onSave={(change, options) => saveLesson(module, lesson, change, options)}
          onActivityChange={handleAssetActivity}
          onLiveChange={handleLive}
          onCommitted={handleAssetCommitted}
        />)}
        {!selected ? <p className="curriculum-lesson-placeholder">Escolha uma aula na lista para pôr vídeo, PDF, texto e materiais.</p> : null}
      </div>
      </div>
    </section>
  );
}

function swap<T>(items: readonly T[], first: number, second: number): T[] {
  const copy = [...items];
  [copy[first], copy[second]] = [copy[second], copy[first]];
  return copy;
}

/** A lista completa na ordem nova, sem perder o que está escondido esperando o desfazer. */
function reorderLike<T extends { id: string }>(items: readonly T[], visibleOrder: readonly string[]): T[] {
  const position = new Map(visibleOrder.map((id, index) => [id, index]));
  const visible = items.filter((item) => position.has(item.id))
    .sort((a, b) => (position.get(a.id) ?? 0) - (position.get(b.id) ?? 0));
  let cursor = 0;
  return items.map((item) => (position.has(item.id) ? visible[cursor++] : item));
}

function focusAfterRender(preferred: string, fallback?: string) {
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
    const first = document.querySelector<HTMLButtonElement>(preferred);
    const target = first && !first.disabled ? first : fallback ? document.querySelector<HTMLElement>(fallback) : null;
    target?.focus();
  }));
}

/** Um texto da pessoa dentro de um seletor `[atributo="…"]`, sem quebrar o seletor. */
function cssText(value: string): string {
  return CSS.escape(value);
}
