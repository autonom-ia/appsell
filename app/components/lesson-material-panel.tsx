"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { JOURNEY_COPY } from "@/lib/curriculum/journey-copy";
import { classifyLessonLink, suggestedLinkTitle, type LessonLink } from "@/lib/curriculum/lesson-link";
import { safeLessonLink } from "@/lib/lesson-markup";

import { useScheduleUndo } from "./undo-toast";

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
 * Os links da aula (#649): um campo só para colar, e a máquina descobre o resto.
 *
 * Antes eram dois `<select>` (tipo e provedor), um campo "Endereço (https)" e
 * um diálogo de risco para remover. Agora quem vende cola o link, vê na hora
 * se é um vídeo do YouTube ou do Vimeo, confere o nome que o aluno vai ver e
 * adiciona. Tirar da aula some na hora e pode ser desfeito por 8 segundos.
 *
 * O painel fica fora de qualquer outro formulário da aula: Enter aqui adiciona
 * o link e não mexe no resto.
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
  const [link, setLink] = useState("");
  const [title, setTitle] = useState("");
  const [titleTouched, setTitleTouched] = useState(false);
  const [removedIds, setRemovedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [notice, setNotice] = useState("");
  const scheduleUndo = useScheduleUndo();

  const addButton = useRef<HTMLButtonElement>(null);
  const linkInput = useRef<HTMLInputElement>(null);
  const courseVersionRef = useRef(courseVersion);
  // Uma chave por pedido pendente, guardada até o servidor responder. Repetir
  // depois de uma queda de conexão reenvia o mesmo pedido em vez de criar um
  // segundo link. A chave vale só para o mesmo conteúdo: se a pessoa mudar os
  // campos depois da falha, o pedido é outro e ganha chave nova.
  const createKey = useRef<CreateKey | null>(null);
  const listRequest = useRef<{ controller: AbortController; version: number } | null>(null);
  const listVersion = useRef(0);
  const inFlight = useRef(new Set<AbortController>());

  useEffect(() => { courseVersionRef.current = courseVersion; }, [courseVersion]);

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
      // Uma resposta antiga e lenta nunca sobrescreve uma mais nova.
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

  const detected = classifyLessonLink(link);
  const suggestion = detected ? suggestedLinkTitle(detected) : "";
  const shownTitle = titleTouched ? title : suggestion;

  function openForm() {
    setOpen(true);
    setNotice("");
    setActionState("idle");
    queueMicrotask(() => linkInput.current?.focus());
  }

  function closeForm() {
    setOpen(false);
    setLink("");
    setTitle("");
    setTitleTouched(false);
    setInvalidField("");
    setActionState("idle");
    // A chave fica: se a tentativa anterior chegou ao servidor, adicionar o
    // mesmo link de novo repete aquele pedido em vez de duplicar o link.
    queueMicrotask(() => addButton.current?.focus());
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (actionState === "working" || archived || !csrf) return;
    const finalTitle = shownTitle.trim();
    if (!detected) return invalid("link");
    if (finalTitle.length < 2) return invalid("title");
    const fields = linkFields(detected, finalTitle, courseVersionRef.current);
    createKey.current = nextCreateKey(createKey.current, JSON.stringify(fields), () => crypto.randomUUID());
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
        // Um pedido recusado nunca será aceito como repetição: a chave sai.
        if (failure === "invalid" || failure === "forbidden") createKey.current = null;
        setActionState(failure);
        return;
      }
      createKey.current = null;
      setNotice(`Pronto: “${finalTitle}” está na aula.`);
      setOpen(false);
      setLink("");
      setTitle("");
      setTitleTouched(false);
      setActionState("idle");
      await loadMaterials();
      onCommitted?.();
      queueMicrotask(() => addButton.current?.focus());
    } catch {
      // A chave fica de propósito: a próxima tentativa repete, não duplica.
      setActionState("unavailable");
    } finally {
      clearTimeout(timeout);
      inFlight.current.delete(controller);
    }
  }

  function invalid(field: "link" | "title") {
    setInvalidField(field);
    setActionState("invalid");
    queueMicrotask(() => document.getElementById(`${panelId}-${field}`)?.focus());
  }

  function remove(material: LessonMaterial) {
    if (!csrf) return;
    const archiveCsrf = csrf.archive;
    const key = crypto.randomUUID();
    setNotice("");
    setRemovedIds((current) => new Set(current).add(material.material_id));
    queueMicrotask(() => addButton.current?.focus());
    const restore = () => setRemovedIds((current) => {
      const next = new Set(current);
      next.delete(material.material_id);
      return next;
    });
    scheduleUndo({
      id: `material:${material.material_id}`,
      name: material.title,
      message: `“${material.title}” saiu da aula.`,
      revert: restore,
      commit: async ({ leaving }) => {
        const data = new FormData();
        data.set("_csrf", archiveCsrf);
        data.set("idempotency_key", key);
        data.set("expected_course_version", String(courseVersionRef.current));
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), MUTATION_TIMEOUT_MS);
        try {
          const response = await fetch(`${base}/${encodeURIComponent(material.material_id)}/archive`, {
            method: "POST",
            body: data,
            keepalive: leaving,
            signal: controller.signal
          });
          if (!response.ok) {
            // A versão do curso pode ter andado: a tela relê antes da próxima mudança.
            onCommitted?.();
            throw new Error("material_archive_failed");
          }
        } finally {
          clearTimeout(timeout);
        }
        setMaterials((current) => current.filter((entry) => entry.material_id !== material.material_id));
        restore();
        onCommitted?.();
      }
    });
  }

  const panelId = `lesson-materials-${lessonId}`;
  const busy = actionState === "working";
  const visible = materials.filter((material) => !removedIds.has(material.material_id));
  const linkHelp = linkHint(link, detected, invalidField === "link");

  return <div className="lesson-links" aria-label={`Links de ${lessonTitle}`} role="group">
    {listState === "loading" ? <p className="lesson-files-empty" role="status">Carregando links…</p> : null}
    {listState !== "loading" && listState !== "ready"
      ? <p className="form-error" role="alert">
          <span>{listMessage(listState)}</span>
          {listState === "unavailable" || listState === "error"
            ? <button type="button" className="secondary-button" onClick={() => void loadMaterials()}>Tentar de novo</button>
            : null}
        </p>
      : null}

    {visible.length > 0 ? <ul className="lesson-file-list">
      {visible.map((material) => <li key={material.material_id} data-testid={`material-${material.material_id}`}>
        <article className="lesson-file" aria-label={material.title}>
          <span className="lesson-file-badge" aria-hidden="true">{material.kind === "embed" ? "▶" : "🔗"}</span>
          <div className="lesson-file-meta">
            <strong className="lesson-file-name" dir="auto">{material.title}</strong>
            <span className="lesson-file-detail">
              <span>{materialDetail(material)}</span>
            </span>
          </div>
          {!archived ? <div className="lesson-file-actions">
            <button
              type="button"
              className="quiet-button"
              aria-label={`${JOURNEY_COPY.removeFile}: ${material.title}`}
              onClick={() => remove(material)}
            >{JOURNEY_COPY.removeFile}</button>
          </div> : null}
        </article>
      </li>)}
    </ul> : null}

    <p className="lesson-links-notice" role="status" aria-live="polite">{notice}</p>

    {!archived && listState === "ready" && !open
      ? <div className="lesson-picker">
          <button ref={addButton} type="button" className="secondary-button" onClick={openForm}>{JOURNEY_COPY.addLink}</button>
        </div>
      : null}

    {!archived && listState === "ready" && open ? <form className="lesson-link-form" noValidate onSubmit={(event) => void create(event)}>
      <fieldset disabled={busy}>
        <legend className="sr-only">Novo link para {lessonTitle}</legend>
        <div className="lesson-field">
          <label htmlFor={`${panelId}-link`}>{JOURNEY_COPY.linkLabel}</label>
          <input
            ref={linkInput}
            id={`${panelId}-link`}
            type="url"
            inputMode="url"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            value={link}
            maxLength={2000}
            placeholder="https://"
            aria-invalid={invalidField === "link" ? "true" : undefined}
            aria-describedby={`${panelId}-link-help`}
            onChange={(event) => {
              setLink(event.target.value);
              if (invalidField === "link") setInvalidField("");
            }}
          />
          <p
            id={`${panelId}-link-help`}
            className={`field-help${invalidField === "link" ? " form-error" : ""}${detected?.kind === "embed" ? " is-detected" : ""}`}
            aria-live="polite"
          >{linkHelp}</p>
        </div>
        <div className="lesson-field">
          <label htmlFor={`${panelId}-title`}>{JOURNEY_COPY.linkName}</label>
          <input
            id={`${panelId}-title`}
            value={shownTitle}
            maxLength={160}
            autoComplete="off"
            aria-invalid={invalidField === "title" ? "true" : undefined}
            aria-describedby={invalidField === "title" ? `${panelId}-title-error` : undefined}
            onChange={(event) => {
              setTitle(event.target.value);
              setTitleTouched(true);
              if (invalidField === "title") setInvalidField("");
            }}
          />
          {invalidField === "title"
            ? <p id={`${panelId}-title-error`} className="form-error">{JOURNEY_COPY.linkNameInvalid}</p>
            : null}
        </div>
        <div className="lesson-link-actions">
          <button type="submit" className="primary-button">{busy ? "Adicionando…" : JOURNEY_COPY.saveLink}</button>
          <button type="button" className="quiet-button" onClick={closeForm}>{JOURNEY_COPY.cancel}</button>
        </div>
        {actionState !== "idle" && actionState !== "working" && actionState !== "invalid"
          ? <p className="form-error" role="alert">{actionMessage(actionState)}</p>
          : null}
      </fieldset>
    </form> : null}
  </div>;
}

export interface CreateKey {
  key: string;
  payload: string;
}

/**
 * Decide se a próxima tentativa é repetição ou pedido novo.
 *
 * Manter a chave depois de uma queda de conexão é o que torna a repetição
 * segura. Mas a chave só quer dizer "o mesmo pedido de novo": se a pessoa
 * mudou os campos depois da falha, repetir faria o servidor responder pelo
 * link digitado primeiro enquanto o painel mostra o segundo. Conteúdo mudado
 * ganha chave nova.
 */
export function nextCreateKey(
  current: CreateKey | null,
  payload: string,
  mint: () => string
): CreateKey {
  if (current && current.payload === payload) return current;
  return { key: mint(), payload };
}

function linkFields(link: LessonLink, title: string, courseVersion: number): Record<string, string> {
  return {
    expected_course_version: String(courseVersion),
    kind: link.kind,
    title,
    url: link.kind === "external" ? link.url : "",
    provider: link.kind === "embed" ? link.provider : "",
    video_id: link.kind === "embed" ? link.videoId : ""
  };
}

function linkHint(raw: string, detected: LessonLink | null, showError: boolean): string {
  if (showError) return JOURNEY_COPY.linkInvalid;
  if (detected?.kind === "embed") return detected.provider === "youtube" ? JOURNEY_COPY.linkYoutube : JOURNEY_COPY.linkVimeo;
  if (detected?.kind === "external" && raw.trim()) return `O aluno abre ${detected.host} numa nova aba.`;
  return JOURNEY_COPY.linkHelp;
}

function materialDetail(material: LessonMaterial): string {
  if (material.kind === "embed") {
    return material.provider === "vimeo" ? "Vídeo do Vimeo · o aluno assiste na aula" : "Vídeo do YouTube · o aluno assiste na aula";
  }
  const safe = safeLessonLink(material.url ?? "");
  if (!safe) return "Link";
  const host = new URL(safe).hostname.toLowerCase();
  return `Abre ${host.startsWith("www.") ? host.slice(4) : host}`;
}

function listMessage(state: ListState) {
  if (state === "unauthorized") return JOURNEY_COPY.sessionExpired;
  if (state === "forbidden") return JOURNEY_COPY.noPermission;
  return "Não deu para carregar os links desta aula. Confira a internet e tente de novo.";
}

function actionMessage(state: ActionState) {
  if (state === "conflict") return JOURNEY_COPY.changedElsewhere;
  if (state === "forbidden") return JOURNEY_COPY.noPermission;
  if (state === "unavailable") return JOURNEY_COPY.offline;
  return "Não deu para adicionar o link. Tente de novo.";
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
    const field = payload?.field === "title" ? "title" : "link";
    setInvalidField(field);
    return "invalid";
  }
  return "error";
}
