"use client";

import { ChangeEvent, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { AccessibleDialog } from "./accessible-dialog";

const MAX_PDF_BYTES = 10 * 1024 * 1024;
const ALLOWED_UPLOAD_HEADERS = new Set([
  "content-type",
  "x-amz-checksum-sha256",
  "x-amz-meta-appsell-upload-id"
]);

export const ASSET_DISCARD_REQUEST_EVENT = "appsell:asset-discard-request";

export interface AssetDiscardRequestDetail {
  register(task: Promise<void>): void;
}

type AssetStatus = "pending" | "uploaded" | "verifying" | "ready" | "quarantined" | "rejected" | "archived";
type Phase = "idle" | "loading" | "hashing" | "creating" | "uploading" | "confirming" | "verifying" | "ready" | "failed" | "cancelled";

interface AssetView {
  id: string;
  title: string;
  file_name: string;
  byte_size: number;
  status: AssetStatus;
  safe_reason?: string | null;
}

interface UploadView {
  id: string;
  status: AssetStatus | "cancelled" | "expired";
  file_name: string;
  byte_size: number;
  safe_reason?: string | null;
}

interface AssetListPayload {
  assets: AssetView[];
  uploads: UploadView[];
  csrf: { intent: string; confirm: string; cancel: string; archive: string };
}

export interface UploadIntentPayload {
  upload_id: string;
  asset_id: string;
  upload_url: string;
  upload_origin: string;
  expires_at: string;
  required_headers: Record<string, string>;
}

interface UploadStatusPayload {
  upload_id: string;
  status: UploadView["status"];
  asset?: AssetView | null;
  safe_reason?: string | null;
  retry_after_seconds?: number;
}

interface UploadState {
  phase: Phase;
  progress: number;
  message: string;
  uploadId: string | null;
  fileName: string;
}

type UploadAction =
  | { type: "phase"; phase: Phase; message: string; fileName?: string; uploadId?: string | null }
  | { type: "progress"; progress: number }
  | { type: "reset" };

const initialUploadState: UploadState = {
  phase: "idle",
  progress: 0,
  message: "Selecione um PDF para iniciar.",
  uploadId: null,
  fileName: ""
};

function uploadReducer(state: UploadState, action: UploadAction): UploadState {
  if (action.type === "reset") return initialUploadState;
  if (action.type === "progress") return { ...state, progress: action.progress };
  return {
    ...state,
    phase: action.phase,
    message: action.message,
    fileName: action.fileName ?? state.fileName,
    uploadId: action.uploadId === undefined ? state.uploadId : action.uploadId,
    progress: action.phase === "uploading" ? state.progress : action.phase === "ready" ? 100 : state.progress
  };
}

export function LessonAssetPanel({
  sellerId,
  productId,
  courseId,
  moduleId,
  lessonId,
  lessonTitle,
  courseVersion,
  archived,
  onActivityChange,
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
  onActivityChange: (lessonId: string, value: { dirty: boolean; busy: boolean }) => void;
  onCommitted: () => void;
}) {
  const [state, dispatch] = useReducer(uploadReducer, initialUploadState);
  const [assets, setAssets] = useState<AssetView[]>([]);
  const [uploads, setUploads] = useState<UploadView[]>([]);
  const [csrf, setCsrf] = useState<AssetListPayload["csrf"] | null>(null);
  const [opened, setOpened] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<AssetView | null>(null);
  const [archiveError, setArchiveError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const pollingUploadRef = useRef<string | null>(null);
  const generationRef = useRef(0);
  const intentRequestRef = useRef<Promise<UploadIntentPayload> | null>(null);
  const activeUploadIdRef = useRef<string | null>(null);
  const discardTaskRef = useRef<Promise<void> | null>(null);
  const cancelRequestsRef = useRef(new Map<string, Promise<void>>());
  const progressFrameRef = useRef<number | null>(null);
  const pendingProgressRef = useRef(0);
  const archiveCancelRef = useRef<HTMLButtonElement>(null);
  const summaryRef = useRef<HTMLElement>(null);

  const base = `/api/sellers/${sellerId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/assets`;
  const busy = ["hashing", "creating", "uploading", "confirming", "verifying"].includes(state.phase);
  const dirty = busy || state.phase === "failed";

  useEffect(() => onActivityChange(lessonId, { dirty, busy }), [busy, dirty, lessonId, onActivityChange]);
  useEffect(() => () => onActivityChange(lessonId, { dirty: false, busy: false }), [lessonId, onActivityChange]);

  const stopTransientWork = useCallback(() => {
    generationRef.current += 1;
    xhrRef.current?.abort();
    xhrRef.current = null;
    if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    pollTimerRef.current = null;
    pollingUploadRef.current = null;
    if (progressFrameRef.current !== null) window.cancelAnimationFrame(progressFrameRef.current);
    progressFrameRef.current = null;
  }, []);

  useEffect(() => stopTransientWork, [stopTransientWork]);

  const cancelIntent = useCallback((uploadId: string) => {
    const pending = cancelRequestsRef.current.get(uploadId);
    if (pending) return pending;
    const request = (async () => {
      if (!csrf) throw new Error("missing_cancel_csrf");
      const body = new FormData();
      body.set("_csrf", csrf.cancel);
      body.set("idempotency_key", crypto.randomUUID());
      const response = await fetch(`${base}/uploads/${uploadId}/cancel`, { method: "POST", body, keepalive: true });
      if (!response.ok) throw new Error(`cancel_${response.status}`);
    })();
    cancelRequestsRef.current.set(uploadId, request);
    void request.catch(() => cancelRequestsRef.current.delete(uploadId));
    return request;
  }, [base, csrf]);

  const discardCurrentUpload = useCallback(() => {
    if (discardTaskRef.current) return discardTaskRef.current;
    const uploadId = activeUploadIdRef.current;
    const pendingIntent = intentRequestRef.current;
    stopTransientWork();
    const task = discardPendingAssetUpload(uploadId, pendingIntent, cancelIntent)
      .then(() => {
        activeUploadIdRef.current = null;
        dispatch({ type: "phase", phase: "cancelled", message: "Envio cancelado. Seu curso não foi alterado." });
        if (inputRef.current) inputRef.current.value = "";
        if (uploadId || pendingIntent) onCommitted();
      })
      .catch((error: unknown) => {
        dispatch({ type: "phase", phase: "failed", message: "Não foi possível cancelar o envio com segurança. Tente novamente antes de sair." });
        throw error;
      })
      .finally(() => {
        discardTaskRef.current = null;
      });
    discardTaskRef.current = task;
    return task;
  }, [cancelIntent, onCommitted, stopTransientWork]);

  useEffect(() => {
    const requestDiscard = (event: Event) => {
      const detail = (event as CustomEvent<AssetDiscardRequestDetail>).detail;
      if (!detail?.register) return;
      const active = intentRequestRef.current !== null || activeUploadIdRef.current !== null ||
        ["hashing", "creating", "uploading", "confirming", "verifying"].includes(state.phase);
      if (active) detail.register(discardCurrentUpload());
    };
    window.addEventListener(ASSET_DISCARD_REQUEST_EVENT, requestDiscard);
    return () => window.removeEventListener(ASSET_DISCARD_REQUEST_EVENT, requestDiscard);
  }, [discardCurrentUpload, state.phase, state.uploadId]);

  const load = useCallback(async () => {
    dispatch({ type: "phase", phase: "loading", message: "Carregando materiais…" });
    try {
      const response = await fetch(base, { cache: "no-store" });
      if (!response.ok) throw new Error("asset_list_failed");
      const payload = await response.json() as AssetListPayload;
      if (!validListPayload(payload)) throw new Error("asset_list_invalid");
      setAssets(payload.assets);
      setUploads(payload.uploads);
      setCsrf(payload.csrf);
      setLoaded(true);
      const active = payload.uploads.find((upload) => ["pending", "uploaded", "verifying"].includes(upload.status));
      if (active) {
        activeUploadIdRef.current = active.id;
        dispatch({ type: "phase", phase: "verifying", message: "Arquivo enviado. Verificando segurança…", fileName: active.file_name, uploadId: active.id });
      } else {
        dispatch({ type: "reset" });
      }
    } catch {
      dispatch({ type: "phase", phase: "failed", message: "Não foi possível carregar os materiais. Tente novamente." });
    }
  }, [base]);

  useEffect(() => {
    if (!opened || loaded) return;
    const start = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(start);
  }, [load, loaded, opened]);

  const pollStatus = useCallback((uploadId: string) => {
    if (pollingUploadRef.current === uploadId) return;
    pollingUploadRef.current = uploadId;
    const generation = generationRef.current;
    let attempt = 0;
    const run = async () => {
      if (generation !== generationRef.current) return;
      if (document.hidden) {
        pollTimerRef.current = window.setTimeout(run, 2_000);
        return;
      }
      try {
        const response = await fetch(`${base}/uploads/${uploadId}`, { cache: "no-store" });
        if (!response.ok) throw new Error("status_failed");
        const payload = await response.json() as UploadStatusPayload;
        if (payload.upload_id !== uploadId) throw new Error("status_invalid");
        if (payload.status === "ready" && payload.asset) {
          activeUploadIdRef.current = null;
          setAssets((current) => [payload.asset!, ...current.filter((asset) => asset.id !== payload.asset!.id)]);
          setUploads((current) => current.filter((upload) => upload.id !== uploadId));
          dispatch({ type: "phase", phase: "ready", message: "PDF pronto para publicar.", uploadId });
          pollingUploadRef.current = null;
          if (inputRef.current) inputRef.current.value = "";
          onCommitted();
          return;
        }
        if (["quarantined", "rejected", "cancelled", "expired"].includes(payload.status)) {
          activeUploadIdRef.current = null;
          const message = payload.status === "quarantined"
            ? "Arquivo isolado para análise. Ele não será publicado."
            : payload.status === "rejected"
              ? payload.safe_reason || "Este arquivo não passou na verificação. Escolha outro PDF."
              : "O envio foi interrompido. Seu curso não foi alterado.";
          dispatch({ type: "phase", phase: payload.status === "cancelled" ? "cancelled" : "failed", message, uploadId });
          pollingUploadRef.current = null;
          if (inputRef.current) inputRef.current.value = "";
          onCommitted();
          return;
        }
        dispatch({ type: "phase", phase: "verifying", message: "Arquivo enviado. Verificando segurança…", uploadId });
        const requested = payload.retry_after_seconds;
        const delay = requested && requested >= 1 && requested <= 10
          ? requested * 1_000
          : Math.min(5_000, [1_500, 2_000, 4_000, 5_000][attempt] ?? 5_000);
        attempt += 1;
        pollTimerRef.current = window.setTimeout(run, delay + Math.floor(Math.random() * 250));
      } catch {
        const delay = Math.min(5_000, [1_500, 2_000, 4_000, 5_000][attempt] ?? 5_000);
        attempt += 1;
        pollTimerRef.current = window.setTimeout(run, delay);
      }
    };
    void run();
  }, [base, onCommitted]);

  useEffect(() => {
    if (opened && state.phase === "verifying" && state.uploadId && pollTimerRef.current === null) {
      pollStatus(state.uploadId);
    }
  }, [opened, pollStatus, state.phase, state.uploadId]);

  async function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || busy || archived) return;
    stopTransientWork();
    const generation = generationRef.current;
    if (file.size < 1 || file.size > MAX_PDF_BYTES) {
      dispatch({ type: "phase", phase: "failed", message: "Escolha um PDF de até 10 MiB.", fileName: file.name });
      event.target.value = "";
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf") || (file.type && file.type !== "application/pdf")) {
      dispatch({ type: "phase", phase: "failed", message: "O arquivo precisa ser um PDF válido.", fileName: file.name });
      event.target.value = "";
      return;
    }
    dispatch({ type: "phase", phase: "hashing", message: "Verificando o arquivo neste dispositivo…", fileName: file.name });
    try {
      const bytes = await file.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      if (generation !== generationRef.current) return;
      const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
      await createIntentAndUpload(file, sha256, generation);
    } catch {
      if (generation !== generationRef.current) return;
      dispatch({ type: "phase", phase: "failed", message: "Não foi possível preparar o PDF. Escolha o arquivo novamente." });
    }
  }

  async function createIntentAndUpload(file: File, sha256: string, generation: number) {
    if (!csrf) throw new Error("missing_csrf");
    dispatch({ type: "phase", phase: "creating", message: "Preparando envio seguro…", fileName: file.name });
    const body = new FormData();
    body.set("_csrf", csrf.intent);
    body.set("idempotency_key", crypto.randomUUID());
    body.set("expected_course_version", String(courseVersion));
    body.set("title", file.name.replace(/\.pdf$/i, ""));
    body.set("file_name", file.name);
    body.set("media_type", "application/pdf");
    body.set("byte_size", String(file.size));
    body.set("sha256", sha256);
    const currentAsset = assets.find((asset) => asset.status === "ready");
    if (currentAsset) body.set("existing_asset_id", currentAsset.id);
    const intentRequest = (async () => {
      const response = await fetch(`${base}/upload-intents`, { method: "POST", body });
      if (!response.ok) throw new Error(`intent_${response.status}`);
      const intent = await response.json() as UploadIntentPayload;
      if (!validIntent(intent, window.location.origin)) throw new Error("intent_invalid");
      return intent;
    })();
    intentRequestRef.current = intentRequest;
    let intent: UploadIntentPayload;
    try {
      intent = await intentRequest;
    } finally {
      if (intentRequestRef.current === intentRequest) intentRequestRef.current = null;
    }
    activeUploadIdRef.current = intent.upload_id;
    if (generation !== generationRef.current) {
      await cancelIntent(intent.upload_id);
      activeUploadIdRef.current = null;
      if (!discardTaskRef.current) onCommitted();
      return;
    }
    dispatch({ type: "phase", phase: "uploading", message: "Enviando PDF — 0%", uploadId: intent.upload_id });
    await uploadDirect(file, intent, generation);
  }

  function uploadDirect(file: File, intent: UploadIntentPayload, generation: number) {
    return new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhrRef.current = xhr;
      xhr.open("PUT", intent.upload_url, true);
      xhr.withCredentials = false;
      xhr.timeout = 120_000;
      for (const [name, value] of Object.entries(intent.required_headers)) {
        if (!ALLOWED_UPLOAD_HEADERS.has(name.toLowerCase())) {
          reject(new Error("unsafe_upload_header"));
          return;
        }
        xhr.setRequestHeader(name, value);
      }
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable || generation !== generationRef.current) return;
        pendingProgressRef.current = Math.max(0, Math.min(100, Math.floor((event.loaded / event.total) * 100)));
        if (progressFrameRef.current !== null) return;
        progressFrameRef.current = window.requestAnimationFrame(() => {
          progressFrameRef.current = null;
          const progress = pendingProgressRef.current;
          dispatch({ type: "progress", progress });
          dispatch({ type: "phase", phase: "uploading", message: `Enviando PDF — ${progress}%` });
        });
      };
      xhr.onerror = () => reject(new Error("upload_network"));
      xhr.ontimeout = () => reject(new Error("upload_timeout"));
      xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
      xhr.onload = () => {
        xhrRef.current = null;
        if (generation !== generationRef.current) return resolve();
        if (xhr.status < 200 || xhr.status >= 300) return reject(new Error(`upload_${xhr.status}`));
        void confirmUpload(intent.upload_id, generation).then(resolve, reject);
      };
      xhr.send(file);
    }).catch((error) => {
      if (generation !== generationRef.current || (error instanceof DOMException && error.name === "AbortError")) return;
      dispatch({ type: "phase", phase: "failed", message: "O envio foi interrompido. Seu curso não foi alterado.", uploadId: intent.upload_id });
      throw error;
    });
  }

  async function confirmUpload(uploadId: string, generation: number) {
    if (!csrf) throw new Error("missing_csrf");
    dispatch({ type: "phase", phase: "confirming", message: "Confirmando o envio seguro…", uploadId });
    const body = new FormData();
    body.set("_csrf", csrf.confirm);
    body.set("idempotency_key", crypto.randomUUID());
    const response = await fetch(`${base}/uploads/${uploadId}/confirm`, { method: "POST", body });
    if (response.status !== 202 && !response.ok) throw new Error(`confirm_${response.status}`);
    if (generation !== generationRef.current) return;
    dispatch({ type: "phase", phase: "verifying", message: "Arquivo enviado. Verificando segurança…", uploadId });
    pollStatus(uploadId);
  }

  async function cancelUpload() {
    await discardCurrentUpload().catch(() => undefined);
  }

  async function archiveAsset() {
    if (!archiveTarget || !csrf) return;
    setArchiveError("");
    const body = new FormData();
    body.set("_csrf", csrf.archive);
    body.set("idempotency_key", crypto.randomUUID());
    body.set("expected_course_version", String(courseVersion));
    try {
      const response = await fetch(`${base}/${archiveTarget.id}/archive`, { method: "POST", body });
      if (!response.ok) throw new Error("archive_failed");
      setAssets((current) => current.filter((asset) => asset.id !== archiveTarget.id));
      setArchiveTarget(null);
      dispatch({ type: "phase", phase: "idle", message: "PDF arquivado. Publicações anteriores continuam inalteradas." });
      onCommitted();
      queueMicrotask(() => summaryRef.current?.focus());
    } catch {
      setArchiveError("Não foi possível arquivar. Recarregue os materiais e tente novamente.");
    }
  }

  const readyAsset = assets.find((asset) => asset.status === "ready");
  const summary = !loaded
    ? "Abrir para carregar"
    : readyAsset
      ? "1 PDF pronto"
      : uploads.some((upload) => ["pending", "uploaded", "verifying"].includes(upload.status))
        ? "Verificação em andamento"
        : "Nenhum material";

  return <div className="lesson-assets">
    <details onToggle={(event) => setOpened(event.currentTarget.open)}>
      <summary ref={summaryRef}><span>Materiais da aula</span><small>{summary}</small></summary>
      <div className="lesson-assets-body" aria-busy={busy}>
        {readyAsset ? <article className="asset-status-card state-ready">
          <div><span className="asset-state">PDF pronto para publicar</span><strong dir="auto">{readyAsset.title}</strong><small dir="auto">{readyAsset.file_name} · {formatBytes(readyAsset.byte_size)}</small></div>
          {!archived ? <div className="asset-actions"><button className="secondary-button" type="button" onClick={() => inputRef.current?.click()}>Substituir PDF</button><button className="danger-button" type="button" onClick={() => setArchiveTarget(readyAsset)}>Arquivar PDF</button></div> : null}
        </article> : null}

        {!archived && loaded ? <div className="asset-uploader">
          <label className="asset-file-label" htmlFor={`asset-file-${lessonId}`}>{readyAsset ? "Escolher PDF substituto" : "Adicione um material em PDF"}</label>
          <input ref={inputRef} id={`asset-file-${lessonId}`} type="file" accept="application/pdf,.pdf" onChange={(event) => void selectFile(event)} disabled={busy}/>
          <p className="asset-help">PDF de até 10 MiB. O arquivo só entra em uma nova publicação depois da verificação.</p>
        </div> : null}

        <div className={`asset-feedback state-${state.phase}`} role="status" aria-live="polite" aria-atomic="true" tabIndex={state.phase === "failed" ? -1 : undefined}>
          <span>{state.message}</span>
          {state.fileName ? <small dir="auto">{state.fileName}</small> : null}
          {state.phase === "uploading" ? <><label className="sr-only" htmlFor={`asset-progress-${lessonId}`}>Progresso do envio</label><progress id={`asset-progress-${lessonId}`} aria-label="Progresso do envio" max="100" value={state.progress}>{state.progress}%</progress></> : null}
          {busy ? <button className="secondary-button" type="button" onClick={() => void cancelUpload()}>Cancelar envio</button> : null}
          {state.phase === "failed" && !loaded ? <button className="secondary-button" type="button" onClick={() => void load()}>Recarregar materiais</button> : null}
          {(state.phase === "failed" && loaded) || state.phase === "cancelled" ? <button className="secondary-button" type="button" onClick={() => inputRef.current?.click()}>Tentar novamente</button> : null}
        </div>
      </div>
    </details>

    {archiveTarget ? <AccessibleDialog titleId={`archive-asset-title-${lessonId}`} descriptionId={`archive-asset-description-${lessonId}`} initialFocusRef={archiveCancelRef} returnFocusRef={summaryRef} onClose={() => setArchiveTarget(null)}>
      <p className="eyebrow">Arquivar material</p>
      <h2 id={`archive-asset-title-${lessonId}`}>Arquivar este PDF?</h2>
      <p id={`archive-asset-description-${lessonId}`}>Ele sairá do rascunho da aula <strong>{lessonTitle}</strong>. Publicações anteriores e alunos com acesso a elas continuarão usando a versão já publicada.</p>
      {archiveError ? <p className="form-error" role="alert">{archiveError}</p> : null}
      <div className="dialog-actions"><button ref={archiveCancelRef} className="secondary-button" type="button" onClick={() => setArchiveTarget(null)}>Manter PDF</button><button className="danger-button" type="button" onClick={() => void archiveAsset()}>Arquivar PDF</button></div>
    </AccessibleDialog> : null}
  </div>;
}

function validListPayload(value: AssetListPayload): boolean {
  return Array.isArray(value.assets) && Array.isArray(value.uploads) && Boolean(value.csrf)
    && ["intent", "confirm", "cancel", "archive"].every((key) => typeof value.csrf[key as keyof AssetListPayload["csrf"]] === "string");
}

export function validIntent(
  value: UploadIntentPayload,
  appOrigin = "",
  nowMs = Date.now()
): boolean {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  if (!value || !uuid.test(value.upload_id) || !uuid.test(value.asset_id) || typeof value.upload_url !== "string" || typeof value.upload_origin !== "string") return false;
  let url: URL;
  let uploadOrigin: URL;
  try { url = new URL(value.upload_url); uploadOrigin = new URL(value.upload_origin); } catch { return false; }
  const expiresMs = Date.parse(value.expires_at);
  if (value.upload_origin !== uploadOrigin.origin || url.origin !== uploadOrigin.origin || url.origin === appOrigin
    || (appOrigin.startsWith("https://") && url.protocol !== "https:") || !/^https?:$/.test(url.protocol)
    || url.username || url.password || url.hash || !url.searchParams.has("X-Amz-Signature")
    || !Number.isFinite(expiresMs) || expiresMs <= nowMs || expiresMs > nowMs + 330_000
    || !value.required_headers || typeof value.required_headers !== "object") return false;
  const headers = Object.keys(value.required_headers).map((name) => name.toLowerCase());
  const uniqueHeaders = new Set(headers);
  return uniqueHeaders.size === ALLOWED_UPLOAD_HEADERS.size
    && headers.length === uniqueHeaders.size
    && [...ALLOWED_UPLOAD_HEADERS].every((name) => uniqueHeaders.has(name))
    && Object.values(value.required_headers).every((header) => typeof header === "string" && header.length > 0);
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) {
    return `${(value / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} KiB`;
  }
  return `${(value / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MiB`;
}

export async function discardPendingAssetUpload(
  uploadId: string | null,
  pendingIntent: Promise<UploadIntentPayload> | null,
  cancel: (uploadId: string) => Promise<void>
): Promise<void> {
  const resolvedUploadId = uploadId ?? (pendingIntent ? (await pendingIntent).upload_id : null);
  if (resolvedUploadId) await cancel(resolvedUploadId);
}
