"use client";

import { ChangeEvent, MouseEvent as ReactMouseEvent, ReactNode, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { sha256File } from "../../lib/file-sha256.client";
import {
  acceptedByBothPanels,
  acceptForContentRole,
  assetMediaLimit,
  mediaFitsContentRole,
  assetMediaSpec,
  assetMediaTypeForFile,
  isDownloadableMedia,
  type AssetMediaType
} from "../../lib/asset-media";
import { AccessibleDialog } from "./accessible-dialog";


export const MAX_VERIFICATION_WAIT_MS = 15_000;
const MATERIAL_PREPARATION_MESSAGE = "Arquivo recebido. Verificando segurança…";
const MATERIAL_DELAYED_MESSAGE = "A verificação está demorando mais que o esperado. O arquivo continua sendo processado; você pode continuar editando e verificar novamente em instantes.";
const ALLOWED_UPLOAD_HEADERS = new Set([
  "content-type",
  "x-amz-checksum-sha256",
  "x-amz-meta-appsell-upload-id"
]);

export const ASSET_DISCARD_REQUEST_EVENT = "appsell:asset-discard-request";

/**
 * Um material mudou de painel, e o painel de destino precisa saber.
 *
 * Cada aula renderiza duas instâncias deste componente, cada uma com a própria
 * lista. Mover um PDF de "Conteúdo da aula" para "Anexos e links" tira o card da
 * primeira — e a segunda, se já estiver aberta, não fica sabendo. O material
 * sumiria de vista até recarregar a página, que é exatamente a experiência que
 * esta entrega existe para acabar.
 */
export const LESSON_ASSETS_CHANGED_EVENT = "appsell:lesson-assets-changed";

export interface LessonAssetsChangedDetail {
  lessonId: string;
  /** Quem avisou; a própria instância ignora o próprio aviso. */
  panelId: string;
}

/**
 * O rótulo diz qual formato o aluno vai baixar.
 *
 * Dizia "PDF" em todo card, inclusive num markdown de 10 KiB.
 */
export function downloadToggleLabel(mediaType: string): string {
  return `Permitir que o aluno baixe este ${mediaLabel(mediaType)}`;
}

export interface AssetDiscardRequestDetail {
  register(task: Promise<void>): void;
}

type AssetStatus = "pending" | "uploaded" | "verifying" | "ready" | "quarantined" | "rejected" | "archived";
type Phase = "idle" | "loading" | "hashing" | "creating" | "uploading" | "confirming" | "verifying" | "delayed" | "ready" | "failed" | "cancelled";

interface AssetView {
  id: string;
  title: string;
  file_name: string;
  byte_size: number;
  media_type: AssetMediaType;
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
  message: "Selecione um arquivo para iniciar.",
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
  contentRole,
  onActivityChange,
  onCommitted,
  children
}: {
  sellerId: string;
  productId: string;
  courseId: string;
  moduleId: string;
  lessonId: string;
  lessonTitle: string;
  courseVersion: number;
  archived: boolean;
  /**
   * O papel que este painel representa. Era um `<select>` em cada card — o
   * sistema perguntava ao vendedor uma coisa que o painel em que ele clicou já
   * responde. Aqui ele deixa de ser pergunta e passa a ser o endereço.
   */
  contentRole: AssetView["content_role"];
  onActivityChange: (lessonId: string, value: { dirty: boolean; busy: boolean }) => void;
  onCommitted: () => void;
  /** Links e vídeos, que moram na mesma lista de anexos mas em outra tabela. */
  children?: ReactNode;
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
  const [justReadyId, setJustReadyId] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const refreshSeqRef = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const hashControllerRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const pollingUploadRef = useRef<string | null>(null);
  const verificationStartedAtRef = useRef<number | null>(null);
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
  const applyPanelRoleRef = useRef<(asset: AssetView) => void>(() => {});
  const justReadyRef = useRef<HTMLElement>(null);
  const panelRef = useRef<HTMLDetailsElement>(null);

  // Há dois painéis por aula. Sem o papel no identificador, os dois inputs de
  // arquivo nascem com o mesmo `id`, e um `<label htmlFor>` ativa o primeiro do
  // documento — "Adicionar anexo" abriria o seletor do painel de conteúdo e o
  // arquivo entraria com o papel errado. O mesmo valia para os dois diálogos, e
  // é o que distingue uma instância da outra ao avisar o irmão.
  const panelId = `${lessonId}-${contentRole}`;
  const base = `/api/sellers/${sellerId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/assets`;
  const uploadBusy = ["hashing", "creating", "uploading", "confirming", "verifying", "delayed"].includes(state.phase);
  const busy = policySavingId !== null || uploadBusy;
  const transportBusy = policySavingId !== null || ["hashing", "creating", "uploading", "confirming"].includes(state.phase);
  const dirty = transportBusy;

  const boundedFetch = useCallback(async (input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = 15_000, abortOnStop = true) => {
    return await boundedAssetFetch(input, init, timeoutMs, fetchControllersRef.current, abortOnStop);
  }, []);

  useEffect(() => onActivityChange(lessonId, { dirty, busy: transportBusy }), [dirty, lessonId, onActivityChange, transportBusy]);
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
    verificationStartedAtRef.current = null;
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
        ["hashing", "creating", "uploading", "confirming", "verifying", "delayed"].includes(state.phase);
      if (active) detail.register(discardCurrentUpload());
      if (policyTaskRef.current) detail.register(policyTaskRef.current);
    };
    window.addEventListener(ASSET_DISCARD_REQUEST_EVENT, requestDiscard);
    return () => window.removeEventListener(ASSET_DISCARD_REQUEST_EVENT, requestDiscard);
  }, [discardCurrentUpload, state.phase, state.uploadId]);

  /**
   * Carregar e revalidar não são a mesma coisa.
   *
   * Carregar é a primeira vez: não há nada na tela, e mostrar "Carregando
   * materiais…" é o certo. Revalidar acontece depois que o vendedor fez algo —
   * ali a mesma mensagem apagaria o "pronto para publicar" e faria o painel
   * piscar em cima de quem está olhando.
   *
   * Revalidar é o que faz o material aparecer depois do envio. Antes disto o
   * painel tentava montar a lista sozinho a partir do que vinha na resposta da
   * verificação — o que exigia ter adivinhado certo o formato daquele dado.
   * Perguntar ao servidor dispensa o palpite: a lista vem ordenada e com o
   * papel que de fato está gravado.
   */
  const load = useCallback(async (mode: "initial" | "revalidate" = "initial") => {
    const initial = mode === "initial";
    if (initial) dispatch({ type: "phase", phase: "loading", message: "Carregando materiais…" });
    setRefreshFailed(false);
    // Duas revalidações podem correr juntas — a do envio e a da política, por
    // exemplo. Sem isto vence a **última a responder**, não a mais nova: a
    // resposta antiga chega atrasada e reescreve a lista já correta, e o card
    // some. Seria o defeito original de volta, por outra porta.
    const seq = ++refreshSeqRef.current;
    const atual = () => seq === refreshSeqRef.current;
    try {
      const response = await boundedFetch(base, { cache: "no-store" }, 10_000);
      if (!response.ok) throw new Error("asset_list_failed");
      const payload = await response.json() as AssetListPayload;
      if (!validListPayload(payload)) throw new Error("asset_list_invalid");
      if (!atual()) return;
      setAssets(payload.assets);
      setUploads(payload.uploads);
      setCsrf(payload.csrf);
      setLoaded(true);
      const active = payload.uploads.find((upload) => ["pending", "uploaded", "verifying"].includes(upload.status));
      // Numa revalidação a mensagem é do que o vendedor acabou de fazer, e
      // trocá-la aqui apagaria a resposta que ele estava esperando. A exceção é
      // um envio ativo que este painel não conhece — de outra aba, ou de outra
      // sessão: sem adotá-lo, o resumo diria "preparação em andamento" e
      // ninguém estaria acompanhando.
      if (!initial) {
        const desconhecido = active && !activeUploadIdRef.current && !uploadBusy;
        if (desconhecido) {
          activeUploadIdRef.current = active.id;
          verificationStartedAtRef.current = Date.now();
          dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, fileName: active.file_name, uploadId: active.id });
        }
        return;
      }
      if (active) {
        activeUploadIdRef.current = active.id;
        verificationStartedAtRef.current = Date.now();
        dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, fileName: active.file_name, uploadId: active.id });
      } else {
        verificationStartedAtRef.current = null;
        dispatch({ type: "reset" });
      }
    } catch {
      // Falhar calado aqui devolveria o vendedor à dúvida que este caminho
      // existe para acabar: o arquivo subiu, e a lista não mostra.
      if (!atual()) return;
      if (!initial) return setRefreshFailed(true);
      dispatch({ type: "phase", phase: "failed", message: "Não foi possível carregar os materiais. Tente novamente." });
    }
  }, [base, boundedFetch, uploadBusy]);

  const anunciarMudanca = useCallback(() => {
    window.dispatchEvent(new CustomEvent<LessonAssetsChangedDetail>(
      LESSON_ASSETS_CHANGED_EVENT,
      { detail: { lessonId, panelId } }
    ));
  }, [lessonId, panelId]);

  useEffect(() => {
    const ouvir = (event: Event) => {
      const detail = (event as CustomEvent<LessonAssetsChangedDetail>).detail;
      // Só o painel irmão da mesma aula: quem anunciou já revalidou, e outras
      // aulas não têm nada a ver com esta mudança.
      if (!detail || detail.lessonId !== lessonId || detail.panelId === panelId) return;
      if (!loaded) return;
      void load("revalidate");
    };
    window.addEventListener(LESSON_ASSETS_CHANGED_EVENT, ouvir);
    return () => window.removeEventListener(LESSON_ASSETS_CHANGED_EVENT, ouvir);
  }, [lessonId, load, loaded, panelId]);

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

  /**
   * O papel do painel, aplicado quando o material fica pronto.
   *
   * O banco já deriva o papel do formato e acerta oito dos nove — só o PDF é
   * ambíguo (ebook protegido no leitor ou anexo para baixar), e quem sabe é o
   * painel em que o vendedor clicou. Por isso só chama quando discorda: nos
   * outros oito não sai requisição nenhuma.
   *
   * Vive numa ref porque `pollStatus` é memoizado e `updatePolicy` é recriada a
   * cada render. Pô-la nas dependências recriaria o callback do polling, que
   * guarda o próprio controle de geração — e o laço perderia o fio. A ref é
   * reescrita a cada render, então nunca guarda uma closure velha.
   */
  useEffect(() => {
    applyPanelRoleRef.current = (asset: AssetView) => {
      // Só o formato que cabe nos dois painéis tem papel a escolher. Nos
      // outros o banco já decidiu pelo formato, e insistir aqui desfazia a
      // normalização — um documento voltava a ser conteúdo do curso, que o
      // player não sabe apresentar.
      if (!acceptedByBothPanels(asset.media_type)) return;
      if (asset.content_role === contentRole) return;
      void updatePolicy(asset, contentRole, contentRole === "support_material");
    };
  });

  // O PDF é o único formato que os dois painéis aceitam, e para ele o papel
  // chega do banco como `course_content` e só vira anexo quando o PATCH de
  // política volta. Até lá o card está filtrado para fora deste painel. Sem
  // esperar por ele, o efeito rodava com a lista ainda sem o card e o material
  // perdia o destaque e o foco de vez.
  const justReadyVisible = assets.some((asset) => asset.id === justReadyId
    && asset.status === "ready" && asset.content_role === contentRole);

  useEffect(() => {
    if (!justReadyId) return;
    // Um quadro depois: o card acabou de entrar na lista e o painel ainda vai
    // crescer. Medir antes do layout assentar dá uma posição que deixa de valer
    // no quadro seguinte — e o card fica fora da vista mesmo tendo "rolado".
    const quadro = justReadyVisible ? window.requestAnimationFrame(() => {
      const card = justReadyRef.current;
      if (!card) return;
      // Se o vendedor está trabalhando **fora deste painel**, a verificação
      // terminou enquanto ele fazia outra coisa. Puxar a página e roubar o foco
      // ali faria a próxima tecla dele ir para o lugar errado; o destaque
      // continua, e basta para ele achar o material quando olhar.
      //
      // Dentro do painel é o contrário: ele abriu este painel e clicou no botão
      // de adicionar daqui. O `<summary>` que ele acabou de acionar retém o foco
      // em alguns navegadores, e tratá-lo como "outro controle" impedia o card
      // de receber o foco justamente de quem estava esperando por ele.
      const ativo = document.activeElement;
      const painel = panelRef.current;
      const ocupado = ativo instanceof HTMLElement
        && ativo !== document.body
        && !(painel?.contains(ativo) ?? false);
      if (ocupado) return;
      // `nearest` já é "mova o mínimo necessário": com o card inteiro à vista a
      // página não se mexe; com ele parcialmente visível, rola só o suficiente
      // para completá-lo; fora da vista, traz para dentro.
      //
      // Medir a posição e decidir aqui era mais frágil do que parecia: um
      // quadro depois do render o painel às vezes ainda não assentou, e sob
      // carga a conta saía de uma geometria que deixava de valer no quadro
      // seguinte — o card ficava fora da vista tendo "rolado".
      card.scrollIntoView({ block: "nearest" });
      // O foco leva quem navega por teclado ou leitor de tela até o material
      // que acabou de entrar.
      card.focus({ preventScroll: true });
    }) : null;
    // O destaque some sozinho — marcá-lo para sempre transformaria "este acabou
    // de chegar" em ruído permanente.
    //
    // Os 15 s são higiene, não comportamento: se o PATCH de política falhar, o
    // PDF nunca entra neste painel e nada na tela muda quando o identificador
    // é solto. Ele existe para o estado não ficar preso a um material que já
    // não vai chegar — e por não produzir efeito visível, não tem teste de
    // comportamento. Dizer que tem seria pior do que não ter.
    const limpar = window.setTimeout(() => setJustReadyId(null), justReadyVisible ? 2_500 : 15_000);
    return () => {
      if (quadro !== null) window.cancelAnimationFrame(quadro);
      window.clearTimeout(limpar);
    };
  }, [justReadyId, justReadyVisible]);

  const pollStatus = useCallback((uploadId: string) => {
    if (pollingUploadRef.current === uploadId) return;
    pollingUploadRef.current = uploadId;
    const generation = generationRef.current;
    verificationStartedAtRef.current ??= Date.now();
    let attempt = 0;
    const run = async () => {
      if (generation !== generationRef.current) return;
      pollTimerRef.current = null;
      const elapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
      if (verificationPhaseForElapsed(elapsed) === "delayed") {
        pollingUploadRef.current = null;
        dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
        return;
      }
      if (document.hidden) {
        pollTimerRef.current = window.setTimeout(run, Math.min(2_000, MAX_VERIFICATION_WAIT_MS - elapsed));
        return;
      }
      try {
        const statusTimeout = verificationStatusTimeoutForElapsed(elapsed);
        const response = await boundedFetch(`${base}/uploads/${uploadId}`, { cache: "no-store" }, statusTimeout);
        if (!response.ok) throw new Error("status_failed");
        const payload = await response.json() as UploadStatusPayload;
        if (payload.upload_id !== uploadId) throw new Error("status_invalid");
        const responseElapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
        if (responseElapsed >= MAX_VERIFICATION_WAIT_MS && ["pending", "uploaded", "verifying"].includes(payload.status)) {
          pollingUploadRef.current = null;
          dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
          return;
        }
        if (payload.status === "ready" && payload.asset) {
          activeUploadIdRef.current = null;
          verificationStartedAtRef.current = null;
          replacementTargetRef.current = null;
          applyPanelRoleRef.current(payload.asset);
          setUploads((current) => current.filter((upload) => upload.id !== uploadId));
          dispatch({ type: "phase", phase: "ready", message: `${mediaLabel(payload.asset.media_type)} pronto para publicar.`, uploadId });
          // A lista vem do servidor, não de um palpite sobre a resposta da
          // verificação. É o que faz o material aparecer sem o vendedor ter de
          // recarregar a página para descobrir se subiu.
          void load("revalidate");
          anunciarMudanca();
          // E leva o vendedor até ele: a lista pode ser longa, e um card que
          // aparece fora da tela é indistinguível de um card que não apareceu.
          setJustReadyId(payload.asset.id);
          pollingUploadRef.current = null;
          if (inputRef.current) inputRef.current.value = "";
          onCommitted();
          return;
        }
        if (["quarantined", "rejected", "cancelled", "expired"].includes(payload.status)) {
          activeUploadIdRef.current = null;
          verificationStartedAtRef.current = null;
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
        pollTimerRef.current = window.setTimeout(run, Math.min(delay + Math.floor(Math.random() * 250), MAX_VERIFICATION_WAIT_MS - elapsed));
      } catch {
        if (generation !== generationRef.current) return;
        const retryElapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
        if (verificationPhaseForElapsed(retryElapsed) === "delayed") {
          pollingUploadRef.current = null;
          dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
          return;
        }
        const delay = Math.min(5_000, [1_500, 2_000, 4_000, 5_000][attempt] ?? 5_000);
        attempt += 1;
        pollTimerRef.current = window.setTimeout(run, Math.min(delay, MAX_VERIFICATION_WAIT_MS - retryElapsed));
      }
    };
    void run();
  }, [anunciarMudanca, base, boundedFetch, load, onCommitted]);

  const retryVerification = useCallback(() => {
    const uploadId = state.uploadId ?? activeUploadIdRef.current;
    if (!uploadId) return;
    verificationStartedAtRef.current = Date.now();
    pollingUploadRef.current = null;
    dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, uploadId });
    pollStatus(uploadId);
  }, [pollStatus, state.uploadId]);

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
    // Toda recusa vem antes de qualquer requisição: uma escolha inválida não
    // pode cancelar o envio anterior, que é trabalho válido. E nenhuma delas
    // zera `replacementTargetRef` — o vendedor mirou um card, e perder isso
    // faria o próximo arquivo entrar como material novo em vez de substituir o
    // original, sem nada dizer.
    //
    // O `accept` do input é dica: quem escolhe "todos os arquivos" no seletor
    // do sistema passa por cima dele.
    const mediaType = selectedMediaType(file);
    if (mediaType && !mediaFitsContentRole(mediaType, contentRole)) {
      dispatch({
        type: "phase",
        phase: "failed",
        message: attachments
          ? "Vídeo não é anexo: o aluno não pode baixá-lo. Envie-o em Conteúdo da aula."
          : "Este formato o aluno abre fora da aula. Envie-o em Anexos e links.",
        fileName: file.name
      });
      event.target.value = "";
      return;
    }
    const maximum = assetMediaLimit(mediaType);
    if (!mediaType || file.size < 1 || file.size > maximum) {
      dispatch({ type: "phase", phase: "failed", message: uploadRefusalMessage(file), fileName: file.name });
      event.target.value = "";
      return;
    }
    const previousUploadId = activeUploadIdRef.current;
    if (previousUploadId) {
      try {
        await cancelIntent(previousUploadId);
        activeUploadIdRef.current = null;
        onCommitted();
      } catch {
        dispatch({ type: "phase", phase: "failed", message: "O envio anterior ainda não foi cancelado com segurança. Tente novamente em instantes.", uploadId: previousUploadId });
        // Sem limpar, reescolher o mesmo arquivo não dispara outro `change` e o
        // vendedor fica sem saída.
        event.target.value = "";
        return;
      }
    }
    stopTransientWork();
    const generation = generationRef.current;
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

  async function createIntentAndUpload(file: File, mediaType: AssetMediaType, sha256: string, generation: number) {
    if (!csrf) throw new Error("missing_csrf");
    dispatch({ type: "phase", phase: "creating", message: "Preparando envio seguro…", fileName: file.name });
    const body = new FormData();
    body.set("_csrf", csrf.intent);
    body.set("idempotency_key", crypto.randomUUID());
    body.set("expected_course_version", String(courseVersion));
    // O título viaja até a área do aluno, e a P1.4 da revisão de 23/08/2026
    // proíbe o nome físico do arquivo chegar lá. Por isso ele continua sendo o
    // texto humano da aula; quem distingue um material do outro na tela do
    // vendedor é `file_name`, que só o editor renderiza.
    body.set("title", lessonTitle);
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
    }).catch(async (error) => {
      if (generation !== generationRef.current || (error instanceof DOMException && error.name === "AbortError")) return;
      try {
        await cancelIntent(intent.upload_id);
        activeUploadIdRef.current = null;
        onCommitted();
      } catch {
        dispatch({ type: "phase", phase: "failed", message: "O envio falhou e ainda não foi possível cancelá-lo com segurança. Tente novamente antes de enviar outro arquivo.", uploadId: intent.upload_id });
        throw error;
      }
      dispatch({ type: "phase", phase: "failed", message: "O envio foi interrompido. Seu curso não foi alterado.", uploadId: intent.upload_id });
      throw error;
    });
  }

  async function confirmUpload(uploadId: string, generation: number) {
    if (!csrf) throw new Error("missing_csrf");
    verificationStartedAtRef.current = Date.now();
    dispatch({ type: "phase", phase: "confirming", message: "Confirmando o envio seguro…", uploadId });
    const body = new FormData();
    body.set("_csrf", csrf.confirm);
    body.set("idempotency_key", crypto.randomUUID());
    let response: Response;
    try {
      response = await boundedFetch(`${base}/uploads/${uploadId}/confirm`, { method: "POST", body }, 15_000);
    } catch (error) {
      if (error instanceof Error && error.message === "asset_request_timeout" && generation === generationRef.current) {
        dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
        return;
      }
      throw error;
    }
    if (response.status !== 202 && !response.ok) throw new Error(`confirm_${response.status}`);
    if (generation !== generationRef.current) return;
    const elapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
    if (verificationPhaseForElapsed(elapsed) === "delayed") {
      dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
      return;
    }
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
      dispatch({ type: "phase", phase: "idle", message: "Material removido da aula. Publicações anteriores continuam inalteradas." });
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
    role: AssetView["content_role"],
    downloadAllowed: boolean
  ) {
    if (!csrf || policySavingId || archived) return;
    const safeDownload = isDownloadableMedia(asset.media_type) &&
      role === "support_material" && downloadAllowed;
    const operation = `${asset.id}:${role}:${safeDownload}`;
    const idempotencyKey = policyKeysRef.current.get(operation) ?? crypto.randomUUID();
    policyKeysRef.current.set(operation, idempotencyKey);
    const body = new FormData();
    body.set("_csrf", csrf.policy);
    body.set("idempotency_key", idempotencyKey);
    body.set("expected_course_version", String(courseVersion));
    body.set("content_role", role);
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
        // De novo do servidor, e não uma edição na lista local. Mover um
        // material entre painéis muda de que lista ele faz parte, e uma
        // atualização local só acerta quando ele já está na lista que estamos
        // editando — o que não é o caso de um PDF que acabou de virar anexo.
        void load("revalidate");
        // E o painel de destino precisa saber: é ele que vai mostrar o card.
        anunciarMudanca();
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

  const attachments = contentRole === "support_material";
  const readyAssets = assets.filter(
    (asset) => asset.status === "ready" && asset.content_role === contentRole
  );
  const heading = attachments ? "Anexos e links" : "Conteúdo da aula";
  const noun = attachments ? "anexo" : "conteúdo";
  const otherRole: AssetView["content_role"] = attachments ? "course_content" : "support_material";
  const summary = !loaded
    ? "Abrir para carregar"
    : readyAssets.length > 0
      ? `${readyAssets.length} ${readyAssets.length === 1 ? `${noun} pronto` : `${noun}s prontos`}`
      : uploads.some((upload) => ["pending", "uploaded", "verifying"].includes(upload.status))
        ? "Preparação em andamento"
        : `Nenhum ${noun}`;

  return <div className="lesson-assets">
    <details ref={panelRef} onToggle={(event) => setOpened(event.currentTarget.open)}>
      <summary ref={summaryRef}><span>{heading}</span><small>{summary}</small></summary>
      <p className="asset-panel-hint">{attachments
        ? "Documentos, planilhas, apresentações, links e vídeos. O aluno pode baixar ou abrir."
        : "PDF ou vídeo MP4. Fica protegido dentro da área do aluno, sem download."}</p>
      <div className="lesson-assets-body" aria-busy={busy}>
        {readyAssets.map((asset) => <article className={`asset-status-card state-ready${asset.id === justReadyId ? " state-just-ready" : ""}`} key={asset.id} ref={asset.id === justReadyId ? justReadyRef : undefined} tabIndex={asset.id === justReadyId ? -1 : undefined}>
          <div><span className="asset-state">{mediaLabel(asset.media_type)} pronto para publicar</span><strong dir="auto">{asset.title}</strong><small className="asset-file-name" dir="auto" title={asset.file_name}>{asset.file_name}</small><small dir="auto">{asset.content_role === "support_material" ? "Material de apoio" : "Conteúdo protegido"} · {formatBytes(asset.byte_size)}</small></div>
          {!archived ? <div className="asset-policy-controls">
            {attachments && isDownloadableMedia(asset.media_type)
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
                  {downloadToggleLabel(asset.media_type)}
                </label>
              : <small>Fica disponível apenas dentro da área do aluno, sem download.</small>}
          </div> : null}
          {!archived ? <div className="asset-actions">
            {/* O PDF é o único formato que os dois painéis aceitam: ebook
                protegido no leitor ou anexo para o aluno baixar. Sem esta ação
                o vendedor teria de remover e reenviar o arquivo só para mudar
                de ideia.

                Mover só move: a permissão de download é levada como está e
                continua sendo uma escolha à parte. Indo para o conteúdo da
                aula ela cai para `false` sozinha, porque `updatePolicy` não
                deixa material protegido ser baixável. */}
            {acceptedByBothPanels(asset.media_type)
              ? <button className="secondary-button" type="button" disabled={busy} onClick={() => void updatePolicy(asset, otherRole, asset.download_allowed)}>
                  {attachments ? "Mover para conteúdo da aula" : "Mover para anexos"}
                </button>
              : null}
            <button className="secondary-button" type="button" disabled={busy} onClick={() => openReplacementPicker(asset.id)}>Substituir arquivo</button>
            <button className="danger-button" type="button" disabled={busy} onClick={() => setArchiveTarget(asset)}>Remover</button>
          </div> : null}
        </article>)}
        {policyError ? <p className="form-error" role="alert">{policyError}</p> : null}
        {refreshFailed ? <p className="form-error asset-refresh-failed" role="alert">
          <span>Seu arquivo foi salvo, mas não foi possível atualizar esta lista.</span>
          <button className="secondary-button" type="button" disabled={busy} onClick={() => void load("revalidate")}>Atualizar lista</button>
        </p> : null}
        {children}

        {!archived && loaded ? <div className="asset-uploader">
          <div className="asset-file-picker">
            <label className="asset-file-label" htmlFor={`asset-file-${panelId}`} onClick={() => { replacementTargetRef.current = null; }}>{readyAssets.length > 0 ? `Adicionar outro ${noun}` : `Adicionar ${noun}`}</label>
            <input ref={inputRef} id={`asset-file-${panelId}`} type="file" accept={acceptForContentRole(contentRole)} onClick={resetReplacementOnDirectPickerClick} onChange={(event) => void selectFile(event)} disabled={busy}/>
          </div>
          {/* O rotulo dizia "Adicione um PDF ou vídeo MP4" e o seletor sempre aceitou
    nove formatos. O vendedor lia o rotulo, concluia que documento nao
    cabia, e ia procurar em "Material complementar" — que so faz link e
    video. A capacidade existia; o rotulo escondia. */}
          <p className="asset-help"><strong>PDF</strong> ou <strong>MP4</strong> viram conteúdo da aula, protegido. <strong>DOCX</strong>, <strong>XLSX</strong>, <strong>PPTX</strong>, <strong>TXT</strong>, <strong>MD</strong>, <strong>CSV</strong> e <strong>JSON</strong> entram como material de apoio, que o aluno pode baixar. Uma aula aceita vários. O arquivo só entra em uma nova publicação depois da verificação.</p>
        </div> : null}

        <div className={`asset-feedback state-${state.phase}`} role="status" aria-live="polite" aria-atomic="true" tabIndex={state.phase === "failed" || state.phase === "delayed" ? -1 : undefined}>
          <span>{state.message}</span>
          {state.fileName ? <small>Material selecionado</small> : null}
          {state.phase === "uploading" ? <><label className="sr-only" htmlFor={`asset-progress-${panelId}`}>Progresso do envio</label><progress id={`asset-progress-${panelId}`} aria-label="Progresso do envio" max="100" value={state.progress}>{state.progress}%</progress></> : null}
          {uploadBusy ? <button className="secondary-button" type="button" onClick={() => void cancelUpload()}>Cancelar envio</button> : null}
          {state.phase === "failed" && !loaded ? <button className="secondary-button" type="button" onClick={() => void load()}>Recarregar materiais</button> : null}
          {state.phase === "delayed" ? <button className="secondary-button" type="button" onClick={retryVerification}>Verificar novamente</button> : null}
          {(state.phase === "failed" && loaded) || state.phase === "cancelled" ? <button className="secondary-button" type="button" onClick={() => inputRef.current?.click()}>Tentar novamente</button> : null}
        </div>
      </div>
    </details>

    {archiveTarget ? <AccessibleDialog titleId={`archive-asset-title-${panelId}`} descriptionId={`archive-asset-description-${panelId}`} initialFocusRef={archiveCancelRef} returnFocusRef={summaryRef} onClose={() => { if (!archiving) setArchiveTarget(null); }}>
      <p className="eyebrow">Remover material</p>
      <h2 id={`archive-asset-title-${panelId}`}>Remover este {archiveTarget ? mediaLabel(archiveTarget.media_type) : "material"} da aula?</h2>
      <p id={`archive-asset-description-${panelId}`}>Ele sairá do rascunho da aula <strong>{lessonTitle}</strong>. Publicações anteriores e alunos com acesso a elas continuarão usando a versão já publicada.</p>
      {archiveError ? <p className="form-error" role="alert">{archiveError}</p> : null}
      <div className="dialog-actions"><button ref={archiveCancelRef} className="secondary-button" type="button" disabled={archiving} onClick={() => setArchiveTarget(null)}>Manter na aula</button><button className="danger-button" type="button" disabled={archiving} onClick={() => void archiveAsset()}>{archiving ? "Removendo…" : "Remover da aula"}</button></div>
    </AccessibleDialog> : null}
  </div>;
}

function validListPayload(value: AssetListPayload): boolean {
  return Array.isArray(value.assets) && Array.isArray(value.uploads) && Boolean(value.csrf)
    && value.assets.every((asset) => assetMediaSpec(asset.media_type) !== null
      && (asset.content_role === "course_content" || asset.content_role === "support_material")
      && typeof asset.download_allowed === "boolean"
      && (!asset.download_allowed || (
        asset.content_role === "support_material" && isDownloadableMedia(asset.media_type)
      )))
    && ["intent", "confirm", "cancel", "archive", "policy"].every((key) => typeof value.csrf[key as keyof AssetListPayload["csrf"]] === "string");
}

function uploadRefusalMessage(file: File): string {
  const mediaType = assetMediaTypeForFile(file.name, file.type);
  if (!mediaType) return "Formato não aceito. Envie PDF, MP4, documento, planilha, apresentação ou texto.";
  const limit = assetMediaLimit(mediaType);
  return `Este arquivo passa do limite de ${formatBytes(limit)} para o formato escolhido.`;
}

function selectedMediaType(file: File): AssetMediaType | null {
  return assetMediaTypeForFile(file.name, file.type);
}

function mediaLabel(mediaType: AssetMediaType) {
  if (mediaType === "video/mp4") return "vídeo";
  if (mediaType === "application/pdf") return "PDF";
  return "documento";
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

export function verificationPhaseForElapsed(elapsedMs: number): "verifying" | "delayed" {
  return elapsedMs >= MAX_VERIFICATION_WAIT_MS ? "delayed" : "verifying";
}

export function verificationStatusTimeoutForElapsed(elapsedMs: number): number {
  return Math.max(1, Math.min(10_000, MAX_VERIFICATION_WAIT_MS - Math.max(0, elapsedMs)));
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
