"use client";

import { ChangeEvent, MouseEvent as ReactMouseEvent, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { sha256File } from "../../lib/file-sha256.client";
import { AccessibleDialog } from "./accessible-dialog";

const MAX_PDF_BYTES = 10 * 1024 * 1024;
const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
const MATERIAL_PREPARATION_MESSAGE = "Arquivo recebido. Validando e preparando o material… PDFs longos podem levar até 1 minuto.";
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
  media_type: "application/pdf" | "video/mp4";
  status: AssetStatus;
  content_role: "course_content" | "support_material";
  download_allowed: boolean;
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
  csrf: { intent: string; confirm: string; cancel: string; archive: string; policy: string };
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
  message: "Selecione um PDF ou vídeo MP4 para iniciar.",
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
  const [archiving, setArchiving] = useState(false);
  const [policySavingId, setPolicySavingId] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const hashControllerRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const pollingUploadRef = useRef<string | null>(null);
  const generationRef = useRef(0);
  const intentRequestRef = useRef<Promise<UploadIntentPayload> | null>(null);
  const activeUploadIdRef = useRef<string | null>(null);
  const discardTaskRef = useRef<Promise<void> | null>(null);
  const cancelRequestsRef = useRef(new Map<string, Promise<void>>());
  const policyKeysRef = useRef(new Map<string, string>());
  const policyTaskRef = useRef<Promise<void> | null>(null);
  const replacementTargetRef = useRef<string | null>(null);
  const fetchControllersRef = useRef(new Set<AbortController>());
  const progressFrameRef = useRef<number | null>(null);
  const pendingProgressRef = useRef(0);
  const archiveCancelRef = useRef<HTMLButtonElement>(null);
  const summaryRef = useRef<HTMLElement>(null);

  const base = `/api/sellers/${sellerId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/assets`;
  const uploadBusy = ["hashing", "creating", "uploading", "confirming", "verifying"].includes(state.phase);
  const busy = policySavingId !== null || uploadBusy;
  const dirty = busy || state.phase === "failed";

  const boundedFetch = useCallback(async (input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = 15_000, abortOnStop = true) => {
    return await boundedAssetFetch(input, init, timeoutMs, fetchControllersRef.current, abortOnStop);
  }, []);

  useEffect(() => onActivityChange(lessonId, { dirty, busy }), [busy, dirty, lessonId, onActivityChange]);
  useEffect(() => () => onActivityChange(lessonId, { dirty: false, busy: false }), [lessonId, onActivityChange]);

  const stopTransientWork = useCallback(() => {
    generationRef.current += 1;
    hashControllerRef.current?.abort();
    hashControllerRef.current = null;
    xhrRef.current?.abort();
    xhrRef.current = null;
    for (const controller of fetchControllersRef.current) controller.abort();
    fetchControllersRef.current.clear();
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
      const response = await boundedFetch(`${base}/uploads/${uploadId}/cancel`, { method: "POST", body, keepalive: true }, 10_000, false);
      if (!response.ok) throw new Error(`cancel_${response.status}`);
    })();
    cancelRequestsRef.current.set(uploadId, request);
    void request.catch(() => cancelRequestsRef.current.delete(uploadId));
    return request;
  }, [base, boundedFetch, csrf]);

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
      if (policyTaskRef.current) detail.register(policyTaskRef.current);
    };
    window.addEventListener(ASSET_DISCARD_REQUEST_EVENT, requestDiscard);
    return () => window.removeEventListener(ASSET_DISCARD_REQUEST_EVENT, requestDiscard);
  }, [discardCurrentUpload, state.phase, state.uploadId]);

  const load = useCallback(async () => {
    dispatch({ type: "phase", phase: "loading", message: "Carregando materiais…" });
    try {
      const response = await boundedFetch(base, { cache: "no-store" }, 10_000);
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
        dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, fileName: active.file_name, uploadId: active.id });
      } else {
        dispatch({ type: "reset" });
      }
    } catch {
      dispatch({ type: "phase", phase: "failed", message: "Não foi possível carregar os materiais. Tente novamente." });
    }
  }, [base, boundedFetch]);

  useEffect(() => {
    if (!opened || loaded) return;
    const start = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(start);
  }, [load, loaded, opened]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    const resetReplacement = () => { replacementTargetRef.current = null; };
    input.addEventListener("cancel", resetReplacement);
    return () => input.removeEventListener("cancel", resetReplacement);
  }, [loaded]);

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
        const response = await boundedFetch(`${base}/uploads/${uploadId}`, { cache: "no-store" }, 10_000);
        if (!response.ok) throw new Error("status_failed");
        const payload = await response.json() as UploadStatusPayload;
        if (payload.upload_id !== uploadId) throw new Error("status_invalid");
        if (payload.status === "ready" && payload.asset) {
          activeUploadIdRef.current = null;
          replacementTargetRef.current = null;
          setAssets((current) => [payload.asset!, ...current.filter((asset) => asset.id !== payload.asset!.id)]);
          setUploads((current) => current.filter((upload) => upload.id !== uploadId));
          dispatch({ type: "phase", phase: "ready", message: `${mediaLabel(payload.asset.media_type)} pronto para publicar.`, uploadId });
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
        dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, uploadId });
        const requested = payload.retry_after_seconds;
        const delay = requested && requested >= 1 && requested <= 10
          ? requested * 1_000
          : Math.min(5_000, [1_500, 2_000, 4_000, 5_000][attempt] ?? 5_000);
        attempt += 1;
        pollTimerRef.current = window.setTimeout(run, delay + Math.floor(Math.random() * 250));
      } catch {
        if (generation !== generationRef.current) return;
        const delay = Math.min(5_000, [1_500, 2_000, 4_000, 5_000][attempt] ?? 5_000);
        attempt += 1;
        pollTimerRef.current = window.setTimeout(run, delay);
      }
    };
    void run();
  }, [base, boundedFetch, onCommitted]);

  useEffect(() => {
    if (opened && state.phase === "verifying" && state.uploadId && pollTimerRef.current === null) {
      pollStatus(state.uploadId);
    }
  }, [opened, pollStatus, state.phase, state.uploadId]);

  async function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || busy || archived) {
      replacementTargetRef.current = null;
      return;
    }
    stopTransientWork();
    const generation = generationRef.current;
    const mediaType = selectedMediaType(file);
    const maximum = mediaType === "application/pdf" ? MAX_PDF_BYTES : mediaType === "video/mp4" ? MAX_VIDEO_BYTES : 0;
    if (!mediaType || file.size < 1 || file.size > maximum) {
      dispatch({ type: "phase", phase: "failed", message: "Escolha um PDF de até 10 MiB ou vídeo MP4 de até 100 MiB.", fileName: file.name });
      event.target.value = "";
      return;
    }
    dispatch({ type: "phase", phase: "hashing", message: "Verificando o arquivo neste dispositivo — 0%", fileName: file.name });
    const hashController = new AbortController();
    hashControllerRef.current = hashController;
    try {
      const sha256 = await sha256File(file, hashController.signal, (progress) => {
        if (generation === generationRef.current) {
          dispatch({ type: "phase", phase: "hashing", message: `Verificando o arquivo neste dispositivo — ${progress}%` });
        }
      });
      if (generation !== generationRef.current) return;
      await createIntentAndUpload(file, mediaType, sha256, generation);
    } catch (error) {
      if (generation !== generationRef.current) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      dispatch({ type: "phase", phase: "failed", message: "Não foi possível preparar o arquivo. Escolha-o novamente." });
    } finally {
      if (hashControllerRef.current === hashController) hashControllerRef.current = null;
    }
  }

  function openReplacementPicker(assetId: string) {
    replacementTargetRef.current = assetId;
    inputRef.current?.click();
  }

  function resetReplacementOnDirectPickerClick(event: ReactMouseEvent<HTMLInputElement>) {
    if (event.nativeEvent.isTrusted) replacementTargetRef.current = null;
  }

  async function createIntentAndUpload(file: File, mediaType: "application/pdf" | "video/mp4", sha256: string, generation: number) {
    if (!csrf) throw new Error("missing_csrf");
    dispatch({ type: "phase", phase: "creating", message: "Preparando envio seguro…", fileName: file.name });
    const body = new FormData();
    body.set("_csrf", csrf.intent);
    body.set("idempotency_key", crypto.randomUUID());
    body.set("expected_course_version", String(courseVersion));
    body.set("title", file.name.replace(/[.](pdf|mp4)$/i, ""));
    body.set("file_name", file.name);
    body.set("media_type", mediaType);
    body.set("byte_size", String(file.size));
    body.set("sha256", sha256);
    if (replacementTargetRef.current) {
      body.set("existing_asset_id", replacementTargetRef.current);
    }
    const intentRequest = (async () => {
      // Do not abort an ambiguous intent request during UI cancellation: wait for its
      // bounded response, recover the upload id, then issue the durable cancel call.
      const response = await boundedFetch(`${base}/upload-intents`, { method: "POST", body }, 15_000, false);
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
    dispatch({ type: "phase", phase: "uploading", message: `Enviando ${mediaLabel(mediaType)} — 0%`, uploadId: intent.upload_id });
    await uploadDirect(file, intent, generation);
  }

  function uploadDirect(file: File, intent: UploadIntentPayload, generation: number) {
    return new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhrRef.current = xhr;
      xhr.open("PUT", intent.upload_url, true);
      xhr.withCredentials = false;
      xhr.timeout = 290_000;
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
          dispatch({ type: "phase", phase: "uploading", message: `Enviando arquivo — ${progress}%` });
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
    const response = await boundedFetch(`${base}/uploads/${uploadId}/confirm`, { method: "POST", body }, 15_000);
    if (response.status !== 202 && !response.ok) throw new Error(`confirm_${response.status}`);
    if (generation !== generationRef.current) return;
    dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, uploadId });
    pollStatus(uploadId);
  }

  async function cancelUpload() {
    await discardCurrentUpload().catch(() => undefined);
  }

  async function archiveAsset() {
    if (!archiveTarget || !csrf || archiving) return;
    setArchiving(true);
    setArchiveError("");
    const body = new FormData();
    body.set("_csrf", csrf.archive);
    body.set("idempotency_key", crypto.randomUUID());
    body.set("expected_course_version", String(courseVersion));
    try {
      const response = await boundedFetch(`${base}/${archiveTarget.id}/archive`, { method: "POST", body }, 8_000);
      if (!response.ok) throw new Error("archive_failed");
      setAssets((current) => current.filter((asset) => asset.id !== archiveTarget.id));
      setArchiveTarget(null);
      dispatch({ type: "phase", phase: "idle", message: "Material arquivado. Publicações anteriores continuam inalteradas." });
      onCommitted();
      queueMicrotask(() => summaryRef.current?.focus());
    } catch {
      setArchiveError("Não foi possível arquivar. Recarregue os materiais e tente novamente.");
    } finally {
      setArchiving(false);
    }
  }

  async function updatePolicy(
    asset: AssetView,
    contentRole: AssetView["content_role"],
    downloadAllowed: boolean
  ) {
    if (!csrf || policySavingId || archived) return;
    const safeDownload = asset.media_type === "application/pdf" &&
      contentRole === "support_material" && downloadAllowed;
    const operation = `${asset.id}:${contentRole}:${safeDownload}`;
    const idempotencyKey = policyKeysRef.current.get(operation) ?? crypto.randomUUID();
    policyKeysRef.current.set(operation, idempotencyKey);
    const body = new FormData();
    body.set("_csrf", csrf.policy);
    body.set("idempotency_key", idempotencyKey);
    body.set("expected_course_version", String(courseVersion));
    body.set("content_role", contentRole);
    body.set("download_allowed", String(safeDownload));
    setPolicySavingId(asset.id);
    setPolicyError("");
    const task = (async () => {
      try {
        const response = await boundedFetch(`${base}/${asset.id}/policy`, {
          method: "PATCH",
          body
        }, 8_000);
        if (!response.ok) throw new Error(`policy_${response.status}`);
        setAssets((current) => current.map((entry) => entry.id === asset.id
          ? { ...entry, content_role: contentRole, download_allowed: safeDownload }
          : entry));
        policyKeysRef.current.delete(operation);
        dispatch({
          type: "phase",
          phase: "idle",
          message: safeDownload
            ? "Material de apoio liberado para download na próxima publicação."
            : "Política do material atualizada para a próxima publicação."
        });
        onCommitted();
      } finally {
        setPolicySavingId(null);
      }
    })();
    policyTaskRef.current = task;
    try {
      await task;
    } catch {
      setPolicyError("Não foi possível salvar a permissão. Recarregue e tente novamente.");
    } finally {
      if (policyTaskRef.current === task) policyTaskRef.current = null;
    }
  }

  const readyAssets = assets.filter((asset) => asset.status === "ready");
  const summary = !loaded
    ? "Abrir para carregar"
    : readyAssets.length > 0
      ? `${readyAssets.length} ${readyAssets.length === 1 ? "material pronto" : "materiais prontos"}`
      : uploads.some((upload) => ["pending", "uploaded", "verifying"].includes(upload.status))
        ? "Preparação em andamento"
        : "Nenhum material";

  return <div className="lesson-assets">
    <details onToggle={(event) => setOpened(event.currentTarget.open)}>
      <summary ref={summaryRef}><span>Materiais da aula</span><small>{summary}</small></summary>
      <div className="lesson-assets-body" aria-busy={busy}>
        {readyAssets.map((asset) => <article className="asset-status-card state-ready" key={asset.id}>
          <div><span className="asset-state">{mediaLabel(asset.media_type)} pronto para publicar</span><strong dir="auto">{asset.title}</strong><small dir="auto">{asset.file_name} · {formatBytes(asset.byte_size)}</small></div>
          {!archived ? <div className="asset-policy-controls">
            <label htmlFor={`asset-role-${asset.id}`}>Uso deste material</label>
            <select
              id={`asset-role-${asset.id}`}
              value={asset.content_role}
              disabled={busy}
              onChange={(event) => void updatePolicy(
                asset,
                event.target.value as AssetView["content_role"],
                event.target.value === "support_material" ? asset.download_allowed : false
              )}
            >
              <option value="course_content">Conteúdo do curso — protegido</option>
              <option value="support_material">Material de apoio</option>
            </select>
            {asset.media_type === "application/pdf" && asset.content_role === "support_material"
              ? <label className="asset-download-toggle">
                  <input
                    type="checkbox"
                    checked={asset.download_allowed}
                    disabled={busy}
                    onChange={(event) => void updatePolicy(
                      asset,
                      "support_material",
                      event.target.checked
                    )}
                  />
                  Permitir que o aluno baixe este PDF
                </label>
              : <small>Este material fica disponível apenas dentro da área do aluno.</small>}
          </div> : null}
          {!archived ? <div className="asset-actions"><button className="secondary-button" type="button" disabled={busy} onClick={() => openReplacementPicker(asset.id)}>Substituir {mediaLabel(asset.media_type)}</button><button className="danger-button" type="button" disabled={busy} onClick={() => setArchiveTarget(asset)}>Arquivar {mediaLabel(asset.media_type)}</button></div> : null}
        </article>)}
        {policyError ? <p className="form-error" role="alert">{policyError}</p> : null}

        {!archived && loaded ? <div className="asset-uploader">
          <label className="asset-file-label" htmlFor={`asset-file-${lessonId}`} onClick={() => { replacementTargetRef.current = null; }}>{readyAssets.length > 0 ? "Adicionar outro material" : "Adicione um PDF ou vídeo MP4"}</label>
          <input ref={inputRef} id={`asset-file-${lessonId}`} type="file" accept="application/pdf,.pdf,video/mp4,.mp4" onClick={resetReplacementOnDirectPickerClick} onChange={(event) => void selectFile(event)} disabled={busy}/>
          <p className="asset-help">PDF até 10 MiB ou MP4 até 100 MiB. O arquivo só entra em uma nova publicação depois da verificação.</p>
        </div> : null}

        <div className={`asset-feedback state-${state.phase}`} role="status" aria-live="polite" aria-atomic="true" tabIndex={state.phase === "failed" ? -1 : undefined}>
          <span>{state.message}</span>
          {state.fileName ? <small dir="auto">{state.fileName}</small> : null}
          {state.phase === "uploading" ? <><label className="sr-only" htmlFor={`asset-progress-${lessonId}`}>Progresso do envio</label><progress id={`asset-progress-${lessonId}`} aria-label="Progresso do envio" max="100" value={state.progress}>{state.progress}%</progress></> : null}
          {uploadBusy ? <button className="secondary-button" type="button" onClick={() => void cancelUpload()}>Cancelar envio</button> : null}
          {state.phase === "failed" && !loaded ? <button className="secondary-button" type="button" onClick={() => void load()}>Recarregar materiais</button> : null}
          {(state.phase === "failed" && loaded) || state.phase === "cancelled" ? <button className="secondary-button" type="button" onClick={() => inputRef.current?.click()}>Tentar novamente</button> : null}
        </div>
      </div>
    </details>

    {archiveTarget ? <AccessibleDialog titleId={`archive-asset-title-${lessonId}`} descriptionId={`archive-asset-description-${lessonId}`} initialFocusRef={archiveCancelRef} returnFocusRef={summaryRef} onClose={() => { if (!archiving) setArchiveTarget(null); }}>
      <p className="eyebrow">Arquivar material</p>
      <h2 id={`archive-asset-title-${lessonId}`}>Arquivar este {archiveTarget ? mediaLabel(archiveTarget.media_type) : "material"}?</h2>
      <p id={`archive-asset-description-${lessonId}`}>Ele sairá do rascunho da aula <strong>{lessonTitle}</strong>. Publicações anteriores e alunos com acesso a elas continuarão usando a versão já publicada.</p>
      {archiveError ? <p className="form-error" role="alert">{archiveError}</p> : null}
      <div className="dialog-actions"><button ref={archiveCancelRef} className="secondary-button" type="button" disabled={archiving} onClick={() => setArchiveTarget(null)}>Manter material</button><button className="danger-button" type="button" disabled={archiving} onClick={() => void archiveAsset()}>{archiving ? "Arquivando…" : "Arquivar material"}</button></div>
    </AccessibleDialog> : null}
  </div>;
}

function validListPayload(value: AssetListPayload): boolean {
  return Array.isArray(value.assets) && Array.isArray(value.uploads) && Boolean(value.csrf)
    && value.assets.every((asset) => (asset.media_type === "application/pdf" || asset.media_type === "video/mp4")
      && (asset.content_role === "course_content" || asset.content_role === "support_material")
      && typeof asset.download_allowed === "boolean"
      && (!asset.download_allowed || (
        asset.content_role === "support_material" && asset.media_type === "application/pdf"
      )))
    && ["intent", "confirm", "cancel", "archive", "policy"].every((key) => typeof value.csrf[key as keyof AssetListPayload["csrf"]] === "string");
}

function selectedMediaType(file: File): "application/pdf" | "video/mp4" | null {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".pdf") && (!file.type || file.type === "application/pdf")) return "application/pdf";
  if (lower.endsWith(".mp4") && (!file.type || file.type === "video/mp4")) return "video/mp4";
  return null;
}

function mediaLabel(mediaType: "application/pdf" | "video/mp4") {
  return mediaType === "video/mp4" ? "vídeo" : "PDF";
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

export async function boundedAssetFetch(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
  stopControllers: Set<AbortController>,
  abortOnStop = true
) {
  const controller = new AbortController();
  if (abortOnStop) stopControllers.add(controller);
  let timedOut = false;
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (timedOut && error instanceof DOMException && error.name === "AbortError") {
      throw new Error("asset_request_timeout");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
    if (abortOnStop) stopControllers.delete(controller);
  }
}

export async function discardPendingAssetUpload(
  uploadId: string | null,
  pendingIntent: Promise<UploadIntentPayload> | null,
  cancel: (uploadId: string) => Promise<void>
): Promise<void> {
  const resolvedUploadId = uploadId ?? (pendingIntent ? (await pendingIntent).upload_id : null);
  if (resolvedUploadId) await cancel(resolvedUploadId);
}
