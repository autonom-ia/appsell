"use client";

import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import { isEmbedId } from "@/lib/lesson-embed";
import { safeLessonLink } from "@/lib/lesson-markup";

import { AccessibleDialog } from "./accessible-dialog";

interface LessonMaterial {
  material_id: string;
  kind: "external" | "embed";
  title: string;
  url: string | null;
  provider: "youtube" | "vimeo" | null;
  video_id: string | null;
  position: number;
}

interface CurriculumCsrf {
  create: string;
  archive: string;
}

type ListState = "loading" | "ready" | "unauthorized" | "forbidden" | "unavailable" | "error";
type ActionState = "idle" | "working" | "invalid" | "conflict" | "forbidden" | "unavailable" | "error";

const LOAD_TIMEOUT_MS = 8_000;
const MUTATION_TIMEOUT_MS = 12_000;

/**
 * The seller's list of links and embedded videos for one lesson.
 *
 * The panel deliberately sits outside the lesson's edit form: its fields are not
 * part of that form's submission, and Enter here adds a material instead of
 * saving the lesson.
 */
export function LessonMaterialPanel({
  sellerId,
  productId,
  courseId,
  moduleId,
  lessonId,
  lessonTitle,
  courseVersion,
  archived,
  onCommitted
}: {
  sellerId: string;
  productId: string;
  courseId: string;
  moduleId: string;
  lessonId: string;
  lessonTitle: string;
  courseVersion: number;
  archived: boolean;
  onCommitted?: () => void;
}) {
  const [materials, setMaterials] = useState<LessonMaterial[]>([]);
  const [csrf, setCsrf] = useState<CurriculumCsrf | null>(null);
  const [listState, setListState] = useState<ListState>("loading");
  const [actionState, setActionState] = useState<ActionState>("idle");
  const [invalidField, setInvalidField] = useState("");
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"external" | "embed">("external");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [provider, setProvider] = useState<"youtube" | "vimeo">("youtube");
  const [videoId, setVideoId] = useState("");
  const [archiveTarget, setArchiveTarget] = useState<LessonMaterial | null>(null);

  const listHeading = useRef<HTMLHeadingElement>(null);
  const archiveCancel = useRef<HTMLButtonElement>(null);
  // One key per pending mutation, held until the server answers. A retry after a
  // dropped connection replays the same operation instead of adding a second row.
  //
  // The key is paired with the payload it was minted for. A replay is only a
  // replay of the same thing: if the seller edits the fields after a failed
  // attempt, the old key would make the server answer for the old material while
  // this panel reported the new one, so the edit earns a new key instead.
  const createKey = useRef<{ key: string; payload: string } | null>(null);
  const archiveKeys = useRef(new Map<string, string>());
  const listRequest = useRef<{ controller: AbortController; version: number } | null>(null);
  const listVersion = useRef(0);
  const inFlight = useRef(new Set<AbortController>());

  const base = `/api/sellers/${encodeURIComponent(sellerId)}/products/${encodeURIComponent(productId)}`
    + `/courses/${encodeURIComponent(courseId)}/modules/${encodeURIComponent(moduleId)}`
    + `/lessons/${encodeURIComponent(lessonId)}/materials`;

  const loadMaterials = useCallback(async () => {
    listRequest.current?.controller.abort();
    const version = ++listVersion.current;
    const controller = new AbortController();
    listRequest.current = { controller, version };
    const timeout = setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS);
    try {
      const response = await fetch(base, { cache: "no-store", signal: controller.signal });
      // A slower earlier answer must never overwrite a newer one.
      if (listVersion.current !== version) return;
      if (response.status === 401) return setListState("unauthorized");
      if (response.status === 403) return setListState("forbidden");
      if (response.status === 503) return setListState("unavailable");
      if (!response.ok) return setListState("error");
      const payload = await response.json() as { materials?: LessonMaterial[]; csrf?: CurriculumCsrf };
      if (listVersion.current !== version) return;
      setMaterials(Array.isArray(payload.materials) ? payload.materials : []);
      setCsrf(payload.csrf ?? null);
      setListState("ready");
    } catch {
      if (listVersion.current === version) setListState("unavailable");
    } finally {
      clearTimeout(timeout);
      if (listRequest.current?.version === version) listRequest.current = null;
    }
  }, [base]);

  useEffect(() => {
    void Promise.resolve().then(loadMaterials);
    const pending = inFlight.current;
    return () => {
      listRequest.current?.controller.abort();
      for (const controller of pending) controller.abort();
      pending.clear();
    };
  }, [loadMaterials]);

  function invalidLocalField() {
    if (title.trim().length < 2) return "title";
    if (kind === "external") return safeLessonLink(url.trim()) ? "" : "url";
    return isEmbedId(videoId.trim()) ? "" : "video_id";
  }

  async function create() {
    if (actionState === "working" || archived || !csrf) return;
    const localFailure = invalidLocalField();
    if (localFailure) {
      setInvalidField(localFailure);
      setActionState("invalid");
      return;
    }
    const fields = {
      expected_course_version: String(courseVersion),
      kind,
      title: title.trim(),
      url: kind === "external" ? (safeLessonLink(url.trim()) ?? "") : "",
      provider: kind === "embed" ? provider : "",
      video_id: kind === "embed" ? videoId.trim() : ""
    };
    const payload = JSON.stringify(fields);
    createKey.current = nextCreateKey(createKey.current, payload, () => crypto.randomUUID());
    setActionState("working");
    setInvalidField("");
    const data = new FormData();
    data.set("_csrf", csrf.create);
    data.set("idempotency_key", createKey.current.key);
    for (const [name, value] of Object.entries(fields)) data.set(name, value);
    const controller = new AbortController();
    inFlight.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), MUTATION_TIMEOUT_MS);
    try {
      const response = await fetch(base, { method: "POST", body: data, signal: controller.signal });
      if (!response.ok) {
        const failure = await failureState(response, setInvalidField);
        // A rejected request will never be accepted as a replay, so the key goes.
        if (failure === "invalid" || failure === "forbidden") createKey.current = null;
        setActionState(failure);
        return;
      }
      createKey.current = null;
      setTitle("");
      setUrl("");
      setVideoId("");
      setActionState("idle");
      await loadMaterials();
      onCommitted?.();
      queueMicrotask(() => listHeading.current?.focus());
    } catch {
      // The key is kept on purpose: the next attempt replays rather than repeats.
      setActionState("unavailable");
    } finally {
      clearTimeout(timeout);
      inFlight.current.delete(controller);
    }
  }

  async function archive(material: LessonMaterial) {
    if (actionState === "working" || !csrf) return;
    const existing = archiveKeys.current.get(material.material_id) ?? crypto.randomUUID();
    archiveKeys.current.set(material.material_id, existing);
    setActionState("working");
    setInvalidField("");
    const data = new FormData();
    data.set("_csrf", csrf.archive);
    data.set("idempotency_key", existing);
    data.set("expected_course_version", String(courseVersion));
    const controller = new AbortController();
    inFlight.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), MUTATION_TIMEOUT_MS);
    try {
      const response = await fetch(`${base}/${encodeURIComponent(material.material_id)}/archive`, {
        method: "POST",
        body: data,
        signal: controller.signal
      });
      if (!response.ok) {
        const failure = await failureState(response, setInvalidField);
        if (failure === "invalid" || failure === "forbidden") {
          archiveKeys.current.delete(material.material_id);
        }
        setActionState(failure);
        return;
      }
      archiveKeys.current.delete(material.material_id);
      setActionState("idle");
      // Only a removal that actually happened closes the dialog. Closing it on a
      // refusal would leave the failure in a live region at the foot of the
      // panel while the thing the seller was looking at disappeared, which reads
      // as success.
      setArchiveTarget(null);
      await loadMaterials();
      onCommitted?.();
      queueMicrotask(() => listHeading.current?.focus());
    } catch {
      setActionState("unavailable");
    } finally {
      clearTimeout(timeout);
      inFlight.current.delete(controller);
    }
  }

  function createOnEnter(event: KeyboardEvent<HTMLInputElement | HTMLSelectElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    void create();
  }

  const panelId = `lesson-materials-${lessonId}`;
  const busy = actionState === "working";

  return <section className="lesson-material-panel" aria-labelledby={`${panelId}-title`}>
    <h5 id={`${panelId}-title`} ref={listHeading} tabIndex={-1}>
      Material complementar ({materials.length})
    </h5>

    {listState === "loading" ? <p role="status">Carregando material…</p> : null}
    {listState !== "loading" && listState !== "ready"
      ? <p className="form-error" role="status">{listMessage(listState)}</p>
      : null}

    {listState === "ready" && materials.length === 0
      ? <p className="lesson-material-empty">Nenhum link ou vídeo nesta aula.</p>
      : null}

    {materials.length > 0 ? <ul className="lesson-material-list">
      {materials.map((material) => <li key={material.material_id} data-testid={`material-${material.material_id}`}>
        <span className="lesson-material-kind">{material.kind === "embed" ? "Vídeo" : "Link"}</span>
        <span className="lesson-material-title">{material.title}</span>
        <span className="lesson-material-detail">
          {material.kind === "embed"
            ? `${material.provider ?? "—"} · ${material.video_id ?? "—"}`
            : hostOf(material.url)}
        </span>
        {!archived ? <button
          type="button"
          className="danger-button"
          disabled={busy}
          onClick={() => setArchiveTarget(material)}
          aria-label={`Remover ${material.title}`}
        >Remover</button> : null}
      </li>)}
    </ul> : null}

    {!archived && listState === "ready" ? <div className="lesson-material-add">
      <button
        type="button"
        className="secondary-button"
        aria-expanded={open}
        aria-controls={`${panelId}-form`}
        onClick={() => setOpen((current) => !current)}
      >{open ? "Cancelar" : "Adicionar link ou vídeo"}</button>

      {open ? <div id={`${panelId}-form`} className="lesson-material-form">
        <fieldset aria-label={`Novo material para ${lessonTitle}`} disabled={busy}>
          <label htmlFor={`${panelId}-kind`}>Tipo</label>
          <select
            id={`${panelId}-kind`}
            value={kind}
            onChange={(event) => setKind(event.target.value === "embed" ? "embed" : "external")}
            onKeyDown={createOnEnter}
          >
            <option value="external">Link externo</option>
            <option value="embed">Vídeo incorporado</option>
          </select>

          <label htmlFor={`${panelId}-title`}>Título</label>
          <input
            id={`${panelId}-title`}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={createOnEnter}
            minLength={2}
            maxLength={160}
            aria-invalid={invalidField === "title"}
            aria-describedby={invalidField === "title" ? `${panelId}-title-error` : undefined}
          />
          {invalidField === "title"
            ? <span id={`${panelId}-title-error`} className="form-error">Informe um título entre 2 e 160 caracteres.</span>
            : null}

          {kind === "external" ? <>
            <label htmlFor={`${panelId}-url`}>Endereço (https)</label>
            <input
              id={`${panelId}-url`}
              type="url"
              inputMode="url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              onKeyDown={createOnEnter}
              maxLength={2000}
              placeholder="https://exemplo.com/material"
              aria-invalid={invalidField === "url"}
              aria-describedby={`${panelId}-url-help`}
            />
            <span id={`${panelId}-url-help`} className={invalidField === "url" ? "form-error" : "field-help"}>
              {invalidField === "url"
                ? "O endereço precisa começar com https:// e não pode conter branco nem aspas."
                : "Somente endereços https. O aluno abre em outra aba."}
            </span>
          </> : <>
            <label htmlFor={`${panelId}-provider`}>Provedor</label>
            <select
              id={`${panelId}-provider`}
              value={provider}
              onChange={(event) => setProvider(event.target.value === "vimeo" ? "vimeo" : "youtube")}
              onKeyDown={createOnEnter}
            >
              <option value="youtube">YouTube</option>
              <option value="vimeo">Vimeo</option>
            </select>

            <label htmlFor={`${panelId}-video`}>Identificador do vídeo</label>
            <input
              id={`${panelId}-video`}
              value={videoId}
              onChange={(event) => setVideoId(event.target.value)}
              onKeyDown={createOnEnter}
              maxLength={64}
              placeholder={provider === "youtube" ? "dQw4w9WgXcQ" : "76979871"}
              aria-invalid={invalidField === "video_id"}
              aria-describedby={`${panelId}-video-help`}
            />
            <span id={`${panelId}-video-help`} className={invalidField === "video_id" ? "form-error" : "field-help"}>
              {invalidField === "video_id"
                ? "Use apenas o identificador do vídeo, sem o endereço completo."
                : "Apenas o identificador, não o endereço inteiro. Ele aparece depois de /watch?v= ou /video/."}
            </span>
          </>}

          <button type="button" className="primary-button" disabled={busy} onClick={() => void create()}>
            {busy ? "Adicionando…" : "Adicionar material"}
          </button>
        </fieldset>
      </div> : null}
    </div> : null}

    <p className="lesson-material-feedback" aria-live="polite">{actionMessage(actionState)}</p>

    {archiveTarget ? <AccessibleDialog
      titleId={`${panelId}-archive-title`}
      descriptionId={`${panelId}-archive-description`}
      initialFocusRef={archiveCancel}
      onClose={() => setArchiveTarget(null)}
    >
      <h2 id={`${panelId}-archive-title`}>Remover {archiveTarget.title}?</h2>
      <p id={`${panelId}-archive-description`}>
        O material sai desta aula. O aluno deixa de vê-lo assim que você publicar de novo.
      </p>
      <div className="inline-actions">
        <button ref={archiveCancel} type="button" className="secondary-button" onClick={() => setArchiveTarget(null)}>
          Manter
        </button>
        <button type="button" className="danger-button" disabled={busy} onClick={() => void archive(archiveTarget)}>
          Remover
        </button>
      </div>
    </AccessibleDialog> : null}
  </section>;
}

export interface CreateKey {
  key: string;
  payload: string;
}

/**
 * Decides whether the next attempt is a replay or a new operation.
 *
 * Keeping the key across a dropped connection is what makes a retry safe. But a
 * key only means "the same request again": if the seller edited the fields after
 * the failure, replaying would let the server answer for the material they typed
 * first while this panel reports the one they typed second. A changed payload
 * therefore earns a new key.
 */
export function nextCreateKey(
  current: CreateKey | null,
  payload: string,
  mint: () => string
): CreateKey {
  if (current && current.payload === payload) return current;
  return { key: mint(), payload };
}

function hostOf(url: string | null) {
  const safe = safeLessonLink(url ?? "");
  if (!safe) return "endereço indisponível";
  return new URL(safe).hostname;
}

function listMessage(state: ListState) {
  if (state === "unauthorized") return "Sua sessão expirou. Entre novamente para ver o material.";
  if (state === "forbidden") return "Você não tem permissão para ver o material desta aula.";
  if (state === "unavailable") return "Não foi possível carregar o material agora.";
  return "Não foi possível carregar o material desta aula.";
}

function actionMessage(state: ActionState) {
  if (state === "working") return "Salvando…";
  if (state === "invalid") return "Confira os campos destacados.";
  if (state === "conflict") return "O conteúdo mudou em outra aba. Recarregue a página e tente de novo.";
  if (state === "forbidden") return "Você não tem permissão para alterar o material desta aula.";
  if (state === "unavailable") return "Não foi possível falar com o servidor. Tente de novo.";
  if (state === "error") return "Não foi possível concluir a operação.";
  return "";
}

async function failureState(
  response: Response,
  setInvalidField: (field: string) => void
): Promise<ActionState> {
  if (response.status === 401) return "forbidden";
  if (response.status === 403) return "forbidden";
  if (response.status === 409) return "conflict";
  if (response.status === 503) return "unavailable";
  if (response.status === 422) {
    const payload = await response.json().catch(() => null) as { field?: string } | null;
    setInvalidField(typeof payload?.field === "string" ? payload.field : "");
    return "invalid";
  }
  return "error";
}
