"use client";

import { ChangeEvent, MouseEvent as ReactMouseEvent, ReactNode, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { sha256File } from "../../lib/file-sha256.client";
import {
  acceptedByBothPanels,
  acceptForContentRole,
  assetMediaSpec,
  assetMediaTypeForFile,
  isDownloadableMedia,
  mediaDemonstrative,
  mediaLabel,
  type AssetMediaType
} from "../../lib/asset-media";
import { fileRefusal, humanSize, JOURNEY_COPY, uploadStage } from "../../lib/curriculum/journey-copy";
import type { LiveUpload } from "../../lib/curriculum/lesson-readiness";
import { useScheduleUndo } from "./undo-toast";


export const MAX_VERIFICATION_WAIT_MS = 15_000;
// Depois dos 15 s a tela avisa que está demorando, mas continua perguntando.
// Um vídeo grande leva de 20 s a alguns minutos (baixar, antivírus, preparar e
// antivírus de novo), e a tela prometia "a lista mostra quando ficar pronto"
// sem voltar a perguntar: a pessoa ficava esperando para sempre (13/11/2026).
// Só para de perguntar sozinha depois de 20 min, perto do que o servidor leva
// para desistir de uma verificação, e aí oferece "Conferir de novo".
export const VERIFICATION_GIVE_UP_MS = 20 * 60_000;
const DELAYED_POLL_MS = [5_000, 10_000, 15_000] as const;
const MATERIAL_PREPARATION_MESSAGE = "Quase lá: conferindo o arquivo";
const MATERIAL_DELAYED_MESSAGE = MATERIAL_PREPARATION_MESSAGE;
const VERIFYING_UPLOAD_STATUSES: readonly string[] = ["uploaded", "verifying"];
const ALLOWED_UPLOAD_HEADERS = new Set([
  "content-type",
  "x-amz-checksum-sha256",
  "x-amz-meta-appsell-upload-id"
]);
/** Uma parte leva só o hash dela: tipo e identificação pertencem ao objeto. */
const ALLOWED_PART_HEADERS = new Set(["x-amz-checksum-sha256"]);

export const ASSET_DISCARD_REQUEST_EVENT = "appsell:asset-discard-request";

/**
 * Um material mudou de painel, e o painel de destino precisa saber.
 *
 * Cada aula renderiza duas instâncias deste componente, cada uma com a própria
 * lista. Mover um PDF de "Vídeo ou PDF da aula" para "Materiais para o aluno" tira o card da
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
 * Identifica um painel de anexos: a aula e o papel. Cada aula tem dois
 * painéis, e o id da aula sozinho não distingue um do outro — nem para o
 * `id` dos inputs, nem para o aviso de atividade ao currículo (#612).
 */
export function assetPanelKey(lessonId: string, contentRole: AssetView["content_role"]): string {
  return `${lessonId}-${contentRole}`;
}

export function downloadToggleLabel(mediaType: unknown): string {
  return `Permitir que o aluno baixe ${mediaDemonstrative(mediaType)} ${mediaLabel(mediaType)}`;
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
  csrf: { intent: string; confirm: string; cancel: string; archive: string; policy: string; signPart: string };
}

export interface UploadIntentPayload {
  upload_id: string;
  asset_id: string;
  upload_url?: string;
  upload_origin?: string;
  expires_at: string;
  required_headers?: Record<string, string>;
  /** Presente só no envio fatiado, e então `upload_url` não existe. */
  multipart?: { part_bytes: number; part_count: number };
}

interface UploadPartPayload {
  part_number: number;
  upload_url: string;
  upload_origin: string;
  expires_at: string;
  required_headers: Record<string, string>;
  required_content_length: number;
}

/** Quantas partes sobem ao mesmo tempo.
 *
 * Três, e não mais: o ganho vem de não deixar a linha ociosa entre requisições,
 * não de abrir conexões. Acima disso elas competem pela mesma banda e a barra
 * de progresso passa a andar em solavancos. */
const PART_CONCURRENCY = 3;

/** Quantas vezes uma parte insiste antes de derrubar o envio.
 *
 * A parte é a unidade de falha: uma queda de rede custa uma parte, e repetir
 * uma parte é barato. É isto que substitui perder o arquivo inteiro. */
const PART_ATTEMPTS = 4;
const PART_RETRY_BASE_MS = 800;
/** A janela que uma intenção fatiada pode declarar: o prazo do banco mais folga. */
const MAX_MULTIPART_WINDOW_MS = 2 * 60 * 60 * 1000 + 300_000;

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
  /** O que fazer agora, quando a mensagem sozinha não diz. */
  detail: string;
  uploadId: string | null;
  fileName: string;
}

type UploadAction =
  | { type: "phase"; phase: Phase; message: string; detail?: string; fileName?: string; uploadId?: string | null }
  | { type: "progress"; progress: number }
  | { type: "reset" };

const initialUploadState: UploadState = {
  phase: "idle",
  progress: 0,
  message: "",
  detail: "",
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
    detail: action.detail ?? "",
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
  onLiveChange,
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
  onActivityChange: (panelKey: string, value: { dirty: boolean; busy: boolean }) => void;
  /** O envio desta aba, para a lista do curso mostrar "Enviando 42%" (#649). */
  onLiveChange?: (value: LiveUpload) => void;
  onCommitted: () => void;
  /** Links e vídeos, que moram na mesma lista de anexos mas em outra tabela. */
  children?: ReactNode;
}) {
  const [state, dispatch] = useReducer(uploadReducer, initialUploadState);
  const [assets, setAssets] = useState<AssetView[]>([]);
  const [csrf, setCsrf] = useState<AssetListPayload["csrf"] | null>(null);
  const [loaded, setLoaded] = useState(false);
  // Tirado da aula e esperando o "Desfazer": some da tela antes do pedido sair.
  const [removedIds, setRemovedIds] = useState<ReadonlySet<string>>(new Set());
  const scheduleUndo = useScheduleUndo();
  const [policySavingId, setPolicySavingId] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState("");
  const [justReadyId, setJustReadyId] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const refreshSeqRef = useRef(0);
  // `loaded` lido e escrito no instante da resposta, não no da criação da
  // closure nem num efeito depois do commit: é a diferença entre saber se ESTA
  // resposta é a primeira lista a chegar.
  const loadedRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const hashControllerRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const pollingUploadRef = useRef<string | null>(null);
  // Cada laço de perguntas tem um número. "Conferir de novo" começa outro, e o
  // anterior, se ainda tiver um pedido no ar, para quando ele voltar.
  const pollLoopRef = useRef(0);
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
  const focusAnchorRef = useRef<HTMLDivElement>(null);
  const courseVersionRef = useRef(courseVersion);
  const applyPanelRoleRef = useRef<(asset: AssetView) => void>(() => {});
  const justReadyRef = useRef<HTMLElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Há dois painéis por aula. Sem o papel no identificador, os dois inputs de
  // arquivo nascem com o mesmo `id`, e um `<label htmlFor>` ativa o primeiro do
  // documento — "Adicionar anexo" abriria o seletor do painel de conteúdo e o
  // arquivo entraria com o papel errado. O mesmo valia para os dois diálogos, e
  // é o que distingue uma instância da outra ao avisar o irmão.
  const panelId = assetPanelKey(lessonId, contentRole);
  const base = `/api/sellers/${sellerId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/assets`;
  const uploadBusy = ["hashing", "creating", "uploading", "confirming", "verifying", "delayed"].includes(state.phase);
  const busy = policySavingId !== null || uploadBusy;
  const transportBusy = policySavingId !== null || ["hashing", "creating", "uploading", "confirming"].includes(state.phase);
  const dirty = transportBusy;

  const boundedFetch = useCallback(async (input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = 15_000, abortOnStop = true) => {
    return await boundedAssetFetch(input, init, timeoutMs, fetchControllersRef.current, abortOnStop);
  }, []);

  // A chave é o painel: os dois painéis da aula avisam o mesmo currículo, e
  // com a chave da aula um apagava o aviso do outro (#612).
  useEffect(() => onActivityChange(panelId, { dirty, busy: transportBusy }), [dirty, onActivityChange, panelId, transportBusy]);
  useEffect(() => () => onActivityChange(panelId, { dirty: false, busy: false }), [onActivityChange, panelId]);
  useEffect(() => { onLiveChange?.({ phase: state.phase, progress: state.progress }); }, [onLiveChange, state.phase, state.progress]);
  useEffect(() => () => onLiveChange?.({ phase: "idle", progress: 0 }), [onLiveChange]);
  useEffect(() => { courseVersionRef.current = courseVersion; }, [courseVersion]);

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
        dispatch({ type: "phase", phase: "cancelled", message: "Envio parado. Nada mudou na aula." });
        if (inputRef.current) inputRef.current.value = "";
        if (uploadId || pendingIntent) onCommitted();
      })
      .catch((error: unknown) => {
        dispatch({ type: "phase", phase: "failed", message: "Não deu para parar o envio agora.", detail: "Espere alguns segundos e tente de novo antes de sair desta tela." });
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
    if (initial) dispatch({ type: "phase", phase: "loading", message: "Carregando os arquivos…" });
    setRefreshFailed(false);
    // Duas revalidações podem correr juntas — a do envio e a da política, por
    // exemplo. Sem isto vence a **última a responder**, não a mais nova: a
    // resposta antiga chega atrasada e reescreve a lista já correta, e o card
    // some. Seria o defeito original de volta, por outra porta.
    const seq = ++refreshSeqRef.current;
    const atual = () => seq === refreshSeqRef.current;
    try {
      // Quem entrega a primeira lista tem de fechar o carregamento, mesmo tendo
      // sido pedida como revalidação. Um aviso do painel irmão pode vencer o
      // carregamento inicial ainda em voo — e sem isto a lista aparecia com
      // "Carregando materiais…" pendurado embaixo, para sempre.
      const response = await boundedFetch(base, { cache: "no-store" }, 10_000);
      if (!response.ok) throw new Error("asset_list_failed");
      const payload = await response.json() as AssetListPayload;
      if (!validListPayload(payload)) throw new Error("asset_list_invalid");
      if (!atual()) return;
      const primeiraEntrega = initial || !loadedRef.current;
      setAssets(payload.assets);
      setCsrf(payload.csrf);
      setLoaded(true);
      // No mesmo instante, não num efeito depois do commit: entre `setLoaded` e
      // o efeito cabe uma revalidação inteira, e ela se acharia a primeira
      // entrega — apagando com `reset` a mensagem que o vendedor está lendo.
      loadedRef.current = true;
      // Fora isso, numa revalidação a mensagem é do que o vendedor acabou de
      // fazer, e trocá-la aqui apagaria a resposta que ele estava esperando.
      //
      // Limite conhecido e aceito: um envio começado em OUTRA aba aparece no
      // resumo sem que este painel o acompanhe, até ele ser reaberto. Tentei
      // adotá-lo aqui e o remédio saiu pior — a decisão dependia de um
      // `uploadBusy` capturado quando `load` foi criado, não de quando a
      // resposta chegou, e um envio remoto podia ser adotado por cima de um
      // envio local recém-começado, os dois dividindo as mesmas referências de
      // controle. Um caso de outra aba não paga esse preço.
      if (!primeiraEntrega) return;
      // Só `uploaded` e `verifying` têm arquivo no servidor sendo conferido.
      // Um `pending` na carga é um envio que ESTE painel não está fazendo: ou
      // parou no meio, ou ainda sobe em outra aba. O servidor não distingue os
      // dois. Tratá-lo como verificação travava o seletor por até duas horas
      // sem nada para esperar (#645); o aviso descreve os dois casos e deixa a
      // pessoa escolher. Vencido, ele já chega `expired` da API.
      const active = payload.uploads.find((upload) => VERIFYING_UPLOAD_STATUSES.includes(upload.status));
      const interrupted = payload.uploads.find((upload) => upload.status === "pending");
      if (active) {
        activeUploadIdRef.current = active.id;
        verificationStartedAtRef.current = Date.now();
        dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, fileName: active.file_name, uploadId: active.id });
      } else if (interrupted) {
        verificationStartedAtRef.current = null;
        dispatch({ type: "phase", phase: "failed", message: `O envio de ${interrupted.file_name} não terminou.`, detail: "Se ele ainda estiver subindo em outra aba, espere por lá. Se não, escolha o arquivo de novo." });
      } else {
        verificationStartedAtRef.current = null;
        dispatch({ type: "reset" });
      }
    } catch {
      // Falhar calado aqui devolveria o vendedor à dúvida que este caminho
      // existe para acabar: o arquivo subiu, e a lista não mostra.
      if (!atual()) return;
      if (!initial && loadedRef.current) return setRefreshFailed(true);
      dispatch({ type: "phase", phase: "failed", message: "Não deu para carregar os arquivos desta aula.", detail: "Confira a internet e tente de novo." });
    }
  }, [base, boundedFetch]);

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
      // Aberto basta: se o carregamento inicial ainda está em voo, ele responde
      // com o retrato de antes da mudança, e a sequência mais nova o descarta.
      // Fechado não precisa — ele carrega ao abrir.
      void load("revalidate");
    };
    window.addEventListener(LESSON_ASSETS_CHANGED_EVENT, ouvir);
    return () => window.removeEventListener(LESSON_ASSETS_CHANGED_EVENT, ouvir);
  }, [lessonId, load, panelId]);

  useEffect(() => {
    if (loaded) return;
    const start = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(start);
  }, [load, loaded]);

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
    let limpar: number | null = null;
    const soltarDestaque = (depoisDeMs: number) => {
      limpar = window.setTimeout(() => setJustReadyId(null), depoisDeMs);
    };
    // Um quadro depois: o card acabou de entrar na lista e o painel ainda vai
    // crescer. Medir antes do layout assentar dá uma posição que deixa de valer
    // no quadro seguinte — e o card fica fora da vista mesmo tendo "rolado".
    const quadro = justReadyVisible ? window.requestAnimationFrame(() => {
      // O destaque conta a partir do quadro em que o card apareceu. Se o
      // relógio começasse antes, um aparelho lento soltaria o destaque, e com
      // ele o quadro do foco, antes de o card receber o foco (#605).
      soltarDestaque(2_500);
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
      //
      // Um título com `tabIndex={-1}` não é controle em uso: é onde a troca de
      // tela pôs o foco (o título da aula ao abrir). Tratá-lo como ocupado
      // deixava o card sem foco para quem acabou de escolher o arquivo aqui.
      const ativo = document.activeElement;
      const painel = panelRef.current;
      const ocupado = ativo instanceof HTMLElement
        && ativo !== document.body
        && ativo.tabIndex >= 0
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
    if (!justReadyVisible) soltarDestaque(15_000);
    return () => {
      if (quadro !== null) window.cancelAnimationFrame(quadro);
      if (limpar !== null) window.clearTimeout(limpar);
    };
  }, [justReadyId, justReadyVisible]);

  const pollStatus = useCallback((uploadId: string) => {
    if (pollingUploadRef.current === uploadId) return;
    pollingUploadRef.current = uploadId;
    const generation = generationRef.current;
    const loop = ++pollLoopRef.current;
    verificationStartedAtRef.current ??= Date.now();
    let attempt = 0;
    const run = async () => {
      if (generation !== generationRef.current || loop !== pollLoopRef.current) return;
      pollTimerRef.current = null;
      const elapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
      if (elapsed >= VERIFICATION_GIVE_UP_MS) {
        pollingUploadRef.current = null;
        dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
        return;
      }
      if (document.hidden) {
        pollTimerRef.current = window.setTimeout(run, 2_000);
        return;
      }
      try {
        const statusTimeout = verificationStatusTimeoutForElapsed(elapsed);
        const response = await boundedFetch(`${base}/uploads/${uploadId}`, { cache: "no-store" }, statusTimeout);
        if (!response.ok) throw new Error("status_failed");
        const payload = await response.json() as UploadStatusPayload;
        if (generation !== generationRef.current || loop !== pollLoopRef.current) return;
        if (payload.upload_id !== uploadId) throw new Error("status_invalid");
        const responseElapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
        if (payload.status === "ready" && payload.asset) {
          activeUploadIdRef.current = null;
          verificationStartedAtRef.current = null;
          replacementTargetRef.current = null;
          applyPanelRoleRef.current(payload.asset);
          dispatch({ type: "phase", phase: "ready", message: `Pronto: ${payload.asset.file_name} entrou na aula.`, uploadId });
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
          const [message, detail] = payload.status === "quarantined"
            ? ["Este arquivo não pode entrar no curso.", "Ele foi separado por segurança. Escolha outro arquivo."]
            : payload.status === "rejected"
              ? ["Este arquivo não abriu.", payload.safe_reason || "Confira se ele abre no seu aparelho e escolha de novo."]
              : ["O envio parou antes de terminar.", "Nada mudou na aula. Escolha o arquivo de novo."];
          dispatch({ type: "phase", phase: payload.status === "cancelled" ? "cancelled" : "failed", message, detail, uploadId });
          pollingUploadRef.current = null;
          if (inputRef.current) inputRef.current.value = "";
          onCommitted();
          return;
        }
        const phase = verificationPhaseForElapsed(responseElapsed);
        dispatch({
          type: "phase",
          phase,
          message: phase === "delayed" ? MATERIAL_DELAYED_MESSAGE : MATERIAL_PREPARATION_MESSAGE,
          uploadId
        });
        const requested = payload.retry_after_seconds;
        const delay = requested && requested >= 1 && requested <= 10
          ? requested * 1_000
          : nextVerificationPollMs(responseElapsed, attempt);
        attempt += 1;
        pollTimerRef.current = window.setTimeout(run, delay + Math.floor(Math.random() * 250));
      } catch {
        if (generation !== generationRef.current || loop !== pollLoopRef.current) return;
        const retryElapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
        if (retryElapsed >= VERIFICATION_GIVE_UP_MS) {
          pollingUploadRef.current = null;
          dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
          return;
        }
        if (verificationPhaseForElapsed(retryElapsed) === "delayed") {
          dispatch({ type: "phase", phase: "delayed", message: MATERIAL_DELAYED_MESSAGE, uploadId });
        }
        const delay = nextVerificationPollMs(retryElapsed, attempt);
        attempt += 1;
        pollTimerRef.current = window.setTimeout(run, delay);
      }
    };
    void run();
  }, [anunciarMudanca, base, boundedFetch, load, onCommitted]);

  const retryVerification = useCallback(() => {
    const uploadId = state.uploadId ?? activeUploadIdRef.current;
    if (!uploadId) return;
    verificationStartedAtRef.current = Date.now();
    if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    pollTimerRef.current = null;
    pollingUploadRef.current = null;
    dispatch({ type: "phase", phase: "verifying", message: MATERIAL_PREPARATION_MESSAGE, uploadId });
    pollStatus(uploadId);
  }, [pollStatus, state.uploadId]);

  useEffect(() => {
    if ((state.phase === "verifying" || state.phase === "delayed") && state.uploadId
        && pollTimerRef.current === null && pollingUploadRef.current === null) {
      pollStatus(state.uploadId);
    }
  }, [pollStatus, state.phase, state.uploadId]);

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
    const refusal = fileRefusal({ name: file.name, size: file.size, type: file.type }, contentRole);
    if (refusal.refused || !mediaType) {
      dispatch({
        type: "phase",
        phase: "failed",
        message: refusal.refused ? refusal.title : "Este tipo de arquivo não entra.",
        detail: refusal.refused ? refusal.fix : "",
        fileName: file.name
      });
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
        dispatch({ type: "phase", phase: "failed", message: "O envio anterior ainda não parou.", detail: "Espere alguns segundos e escolha o arquivo de novo.", uploadId: previousUploadId });
        // Sem limpar, reescolher o mesmo arquivo não dispara outro `change` e o
        // vendedor fica sem saída.
        event.target.value = "";
        return;
      }
    }
    stopTransientWork();
    const generation = generationRef.current;
    dispatch({ type: "phase", phase: "hashing", message: "Preparando o arquivo", fileName: file.name });
    const hashController = new AbortController();
    hashControllerRef.current = hashController;
    // Ler o arquivo no aparelho e falar com o servidor falham por motivos
    // diferentes, e cada um pede uma saída diferente.
    let reading = true;
    try {
      const sha256 = await sha256File(file, hashController.signal, (progress) => {
        if (generation === generationRef.current) {
          dispatch({ type: "phase", phase: "hashing", message: `Preparando o arquivo: ${progress}%` });
        }
      });
      if (generation !== generationRef.current) return;
      reading = false;
      await createIntentAndUpload(file, mediaType, sha256, generation);
    } catch (error) {
      if (generation !== generationRef.current) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      const [message, detail] = reading
        ? ["Não deu para ler este arquivo.", "Confira se ele abre no seu aparelho e escolha de novo."]
        : startFailure(error);
      dispatch({ type: "phase", phase: "failed", message, detail });
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
    // A versão de agora, não a do começo: o hash de um arquivo grande leva
    // segundos, e nesse meio tempo o texto salvo sozinho já mudou a versão.
    body.set("expected_course_version", String(courseVersionRef.current));
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
    dispatch({ type: "phase", phase: "uploading", message: `Enviando ${mediaLabel(mediaType)}: 0%`, uploadId: intent.upload_id });
    if (intent.multipart) {
      await uploadInParts(file, intent, intent.multipart, mediaType, generation);
      return;
    }
    await uploadDirect(file, intent, generation);
  }

  /**
   * O envio fatiado: o arquivo sobe em partes e continua de onde parou.
   *
   * O `PUT` único não serve para uma aula longa. A URL assinada dura 300 s, e
   * numa linha de 10 Mbit/s uma aula de 45 min leva mais de 600 s: ela vencia no
   * meio e o vendedor perdia tudo, sem ter de onde recomeçar. Aqui nenhuma
   * requisição precisa durar mais que uns segundos, e uma queda custa uma parte.
   */
  async function uploadInParts(
    file: File,
    intent: UploadIntentPayload,
    plano: { part_bytes: number; part_count: number },
    mediaType: AssetMediaType,
    generation: number
  ) {
    const tamanhoDaParte = (numero: number) =>
      Math.min(plano.part_bytes, file.size - (numero - 1) * plano.part_bytes);
    const jaEnviadas = await partesJaEnviadas(intent.upload_id);
    const pendentes = Array.from({ length: plano.part_count }, (_, indice) => indice + 1)
      .filter((numero) => !jaEnviadas.has(numero));
    let enviados = [...jaEnviadas].reduce((total, numero) => total + tamanhoDaParte(numero), 0);

    // Uma parte que falha para as outras: sem isto, o trabalhador que ainda
    // corria reescrevia a falha com "Enviando N%" e um botão de parar inútil.
    let parou = false;
    const relatar = () => {
      if (parou || generation !== generationRef.current) return;
      const progresso = Math.max(0, Math.min(100, Math.floor((enviados / file.size) * 100)));
      dispatch({ type: "progress", progress: progresso });
      dispatch({
        type: "phase",
        phase: "uploading",
        message: `Enviando ${mediaLabel(mediaType)}: ${progresso}%`,
        uploadId: intent.upload_id
      });
    };
    relatar();

    let proxima = 0;
    const trabalhador = async () => {
      while (!parou) {
        const indice = proxima;
        proxima += 1;
        if (indice >= pendentes.length) return;
        const numero = pendentes[indice];
        const inicio = (numero - 1) * plano.part_bytes;
        await enviarParte(intent.upload_id, numero, file.slice(inicio, inicio + tamanhoDaParte(numero)), generation);
        enviados += tamanhoDaParte(numero);
        relatar();
      }
    };
    try {
      await Promise.all(
        Array.from({ length: Math.min(PART_CONCURRENCY, Math.max(pendentes.length, 1)) }, trabalhador)
      );
    } catch (error) {
      parou = true;
      // O mesmo desfecho do envio único: cancela a intenção e diz ao vendedor
      // que o curso não mudou. As partes já enviadas somem com o cancelamento.
      if (await falhaDoEnvio(intent.upload_id, generation, error)) throw error;
      return;
    }
    if (generation !== generationRef.current) return;
    await confirmUpload(intent.upload_id, generation);
  }

  /** O que já chegou. Falhar aqui só significa mandar tudo de novo, e não
   * derrubar o envio: retomar é um ganho, não um pré-requisito. */
  async function partesJaEnviadas(uploadId: string): Promise<Set<number>> {
    try {
      const response = await boundedFetch(`${base}/uploads/${uploadId}/parts`, {}, 15_000, false);
      if (!response.ok) return new Set();
      const payload = await response.json() as { uploaded_parts?: { part_number: number }[] };
      return new Set((payload.uploaded_parts ?? []).map((parte) => parte.part_number));
    } catch {
      return new Set();
    }
  }

  async function enviarParte(uploadId: string, numero: number, pedaco: Blob, generation: number) {
    const hash = await sha256File(pedaco);
    for (let tentativa = 1; ; tentativa += 1) {
      if (generation !== generationRef.current) throw new DOMException("Upload cancelled", "AbortError");
      try {
        const assinada = await assinarParte(uploadId, numero, hash);
        await enviarPedaco(assinada, pedaco);
        return;
      } catch (error) {
        // Recusa é definitiva: repetir daria no mesmo, quatro vezes. Só a falha
        // de transporte merece insistência.
        const definitiva = error instanceof DOMException || (error instanceof Error && /^part_(4\d\d|invalid)/.test(error.message));
        if (definitiva || tentativa >= PART_ATTEMPTS) throw error;
        dispatch({ type: "phase", phase: "uploading", message: "Enviando", detail: "A internet oscilou. Tentando de novo…", uploadId });
        await new Promise<void>((resolve) => window.setTimeout(resolve, PART_RETRY_BASE_MS * tentativa));
      }
    }
  }

  async function assinarParte(uploadId: string, numero: number, sha256: string): Promise<UploadPartPayload> {
    if (!csrf) throw new Error("missing_csrf");
    const body = new FormData();
    body.set("_csrf", csrf.signPart);
    body.set("sha256", sha256);
    const response = await boundedFetch(`${base}/uploads/${uploadId}/parts/${numero}`, { method: "POST", body }, 15_000, false);
    if (!response.ok) throw new Error(`part_${response.status}`);
    const payload = await response.json() as UploadPartPayload;
    if (!validPart(payload, window.location.origin)) throw new Error("part_invalid");
    return payload;
  }

  function enviarPedaco(assinada: UploadPartPayload, pedaco: Blob) {
    return new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", assinada.upload_url, true);
      xhr.withCredentials = false;
      xhr.timeout = 290_000;
      for (const [name, value] of Object.entries(assinada.required_headers)) {
        if (!ALLOWED_PART_HEADERS.has(name.toLowerCase())) {
          reject(new Error("part_invalid_header"));
          return;
        }
        xhr.setRequestHeader(name, value);
      }
      xhr.onerror = () => reject(new Error("part_network"));
      xhr.ontimeout = () => reject(new Error("part_timeout"));
      xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
      xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) return reject(new Error(`part_${xhr.status}`));
        resolve();
      };
      xhr.send(pedaco);
    });
  }

  /**
   * O desfecho de um envio que não terminou, igual nos dois caminhos.
   *
   * Devolve `false` quando não há o que fazer — o envio foi cancelado pelo
   * vendedor, ou pertence a uma tentativa anterior — e `true` quando a falha foi
   * comunicada e cabe ao chamador propagá-la.
   */
  async function falhaDoEnvio(uploadId: string, generation: number, error: unknown) {
    if (generation !== generationRef.current || (error instanceof DOMException && error.name === "AbortError")) return false;
    try {
      await cancelIntent(uploadId);
      activeUploadIdRef.current = null;
      onCommitted();
    } catch {
      dispatch({ type: "phase", phase: "failed", message: "O envio parou e ainda não deu para limpar o que subiu.", detail: "Espere alguns segundos e escolha o arquivo de novo.", uploadId });
      return true;
    }
    dispatch({ type: "phase", phase: "failed", message: "O envio parou antes de terminar.", detail: "Confira a internet e escolha o arquivo de novo. Nada foi salvo pela metade.", uploadId });
    return true;
  }

  function uploadDirect(file: File, intent: UploadIntentPayload, generation: number) {
    return new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhrRef.current = xhr;
      if (!intent.upload_url || !intent.required_headers) {
        reject(new Error("upload_intent_without_url"));
        return;
      }
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
          dispatch({ type: "phase", phase: "uploading", message: `Enviando arquivo: ${progress}%` });
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
      if (await falhaDoEnvio(intent.upload_id, generation, error)) throw error;
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
        pollStatus(uploadId);
        return;
      }
      throw error;
    }
    if (response.status !== 202 && !response.ok) throw new Error(`confirm_${response.status}`);
    if (generation !== generationRef.current) return;
    const elapsed = Date.now() - (verificationStartedAtRef.current ?? Date.now());
    const phase = verificationPhaseForElapsed(elapsed);
    dispatch({
      type: "phase",
      phase,
      message: phase === "delayed" ? MATERIAL_DELAYED_MESSAGE : MATERIAL_PREPARATION_MESSAGE,
      uploadId
    });
    pollStatus(uploadId);
  }

  async function cancelUpload() {
    await discardCurrentUpload().catch(() => undefined);
  }

  /**
   * Tirar da aula some na hora e o pedido só sai quando o "Desfazer" vence
   * (#649, decisão D2). Antes era um diálogo de risco com a palavra "rascunho".
   */
  function removeAsset(asset: AssetView) {
    if (!csrf) return;
    const archiveCsrf = csrf.archive;
    setRemovedIds((current) => new Set(current).add(asset.id));
    dispatch({ type: "phase", phase: "idle", message: "" });
    // O foco vai para o que sobrou: o próximo arquivo, ou a escolha de arquivo.
    window.requestAnimationFrame(() => {
      const painel = panelRef.current;
      const vizinho = painel?.querySelector<HTMLElement>(".lesson-file-actions button:not(:disabled)");
      const escolher = inputRef.current && !inputRef.current.disabled ? inputRef.current : null;
      (vizinho ?? escolher ?? focusAnchorRef.current)?.focus();
    });
    const restore = () => setRemovedIds((current) => {
      const next = new Set(current);
      next.delete(asset.id);
      return next;
    });
    scheduleUndo({
      id: `asset:${asset.id}`,
      name: asset.file_name,
      message: `“${asset.file_name}” saiu da aula.`,
      revert: restore,
      commit: async ({ leaving }) => {
        const body = new FormData();
        body.set("_csrf", archiveCsrf);
        body.set("idempotency_key", crypto.randomUUID());
        body.set("expected_course_version", String(courseVersionRef.current));
        const response = await boundedFetch(`${base}/${asset.id}/archive`, { method: "POST", body, keepalive: leaving }, 8_000, false);
        if (!response.ok) {
          // A versão do curso pode ter andado: a tela relê antes da próxima mudança.
          onCommitted();
          throw new Error("archive_failed");
        }
        setAssets((current) => current.filter((entry) => entry.id !== asset.id));
        restore();
        onCommitted();
      }
    });
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
    // A versão de agora, não a do começo: o hash de um arquivo grande leva
    // segundos, e nesse meio tempo o texto salvo sozinho já mudou a versão.
    body.set("expected_course_version", String(courseVersionRef.current));
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
        if (!response.ok) {
          // Recusado não vira repetição: a próxima tentativa é outro pedido,
          // com a versão nova. Só a queda (503) guarda a chave.
          if (response.status !== 503) policyKeysRef.current.delete(operation);
          if (response.status === 409) onCommitted();
          throw new Error(`policy_${response.status}`);
        }
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
          message: role !== asset.content_role
            ? role === "support_material" ? "O PDF foi para Materiais para o aluno." : "O PDF foi para Vídeo ou PDF da aula."
            : safeDownload ? "Pronto: o aluno poderá baixar este arquivo." : "Pronto: o aluno não poderá baixar este arquivo."
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
      setPolicyError("Não deu para salvar esta mudança. Confira a internet e tente de novo.");
    } finally {
      if (policyTaskRef.current === task) policyTaskRef.current = null;
    }
  }

  const attachments = contentRole === "support_material";
  const readyAssets = assets.filter(
    (asset) => asset.status === "ready" && asset.content_role === contentRole && !removedIds.has(asset.id)
  );
  const otherRole: AssetView["content_role"] = attachments ? "course_content" : "support_material";
  const stageVisible = uploadBusy;
  const stage = uploadStage(state.phase, state.progress);
  const showMessage = !stageVisible && state.phase !== "loading" && state.message !== "";
  const pickLabel = attachments
    ? JOURNEY_COPY.addFile
    : readyAssets.length > 0 ? JOURNEY_COPY.pickAnotherMain : JOURNEY_COPY.pickMain;
  const firstChoice = !attachments && readyAssets.length === 0;

  return <div className={`lesson-files lesson-files-${attachments ? "extra" : "main"}`} ref={panelRef} aria-busy={busy}>
    <div ref={focusAnchorRef} tabIndex={-1} className="lesson-files-anchor"/>
    {readyAssets.length > 0 ? <ul className="lesson-file-list">
      {readyAssets.map((asset, index) => <li key={asset.id}>
        <article
          className={`lesson-file${!attachments && index === 0 ? " is-main" : ""}${asset.id === justReadyId ? " state-just-ready" : ""}`}
          ref={asset.id === justReadyId ? justReadyRef : undefined}
          tabIndex={asset.id === justReadyId ? -1 : undefined}
          aria-label={asset.file_name}
        >
          <span className="lesson-file-badge" aria-hidden="true">{fileBadge(asset.media_type)}</span>
          <div className="lesson-file-meta">
            <strong className="lesson-file-name" dir="auto" title={asset.file_name}>{asset.file_name}</strong>
            <span className="lesson-file-detail">
              <span className="course-chip course-chip-ok">✓ Pronto</span>
              <span>{capitalize(mediaLabel(asset.media_type))} · {humanSize(asset.byte_size)}</span>
            </span>
            {attachments && isDownloadableMedia(asset.media_type) && !archived ? <label className="lesson-switch">
              <input
                type="checkbox"
                role="switch"
                checked={asset.download_allowed}
                disabled={busy}
                aria-label={`${JOURNEY_COPY.downloadToggle}: ${asset.file_name}`}
                onChange={(event) => void updatePolicy(asset, "support_material", event.target.checked)}
              />
              <span aria-hidden="true">{JOURNEY_COPY.downloadToggle}</span>
            </label> : null}
          </div>
          {!archived ? <div className="lesson-file-actions">
            {/* O PDF é o único formato que os dois lugares aceitam: lido aqui
                dentro, protegido, ou baixado pelo aluno. Mudar de ideia não
                pode exigir tirar e mandar o arquivo de novo. */}
            {acceptedByBothPanels(asset.media_type)
              ? <button className="secondary-button" type="button" disabled={busy} onClick={() => void updatePolicy(asset, otherRole, asset.download_allowed)}>
                  {attachments ? "Mover para Vídeo ou PDF da aula" : "Mover para Materiais para o aluno"}
                </button>
              : null}
            <button className="secondary-button" type="button" disabled={busy} aria-label={`${JOURNEY_COPY.replaceFile}: ${asset.file_name}`} onClick={() => openReplacementPicker(asset.id)}>{JOURNEY_COPY.replaceFile}</button>
            <button className="quiet-button" type="button" disabled={busy} aria-label={`${JOURNEY_COPY.removeFile}: ${asset.file_name}`} onClick={() => removeAsset(asset)}>{JOURNEY_COPY.removeFile}</button>
          </div> : null}
        </article>
      </li>)}
    </ul> : null}
    {attachments && readyAssets.length === 0 ? <p className="lesson-files-empty">{JOURNEY_COPY.materialsHelp}</p> : null}
    {policyError ? <p className="form-error" role="alert">{policyError}</p> : null}
    {refreshFailed ? <p className="form-error lesson-files-refresh" role="alert">
      <span>O arquivo foi salvo, mas a lista não atualizou.</span>
      <button className="secondary-button" type="button" disabled={busy} onClick={() => void load("revalidate")}>Atualizar lista</button>
    </p> : null}

    {/* O que muda a cada instante (a porcentagem, os botões) fica fora das
        regiões anunciadas: o leitor de tela ouve a fase, não cada número. */}
    <div className={`lesson-upload-box${stageVisible ? " is-active" : ""}${state.phase === "failed" ? " is-failed" : ""}`}>
      <div className="lesson-upload-state" role="status" aria-live="polite" aria-atomic="true">
        {stageVisible ? <>
          <p>
            <strong><span aria-hidden="true">{stage.title}</span><span className="sr-only">{spokenStage(state.phase)}</span></strong>
            {state.fileName ? <span className="lesson-upload-file" dir="auto"> · {state.fileName}</span> : null}
          </p>
          <p className="lesson-upload-detail">{state.detail || stage.detail}</p>
        </> : null}
        {showMessage && state.phase !== "failed" ? <>
          <p><strong>{state.message}</strong></p>
          {state.detail ? <p className="lesson-upload-detail">{state.detail}</p> : null}
        </> : null}
        {state.phase === "loading" ? <p className="lesson-upload-detail">{state.message}</p> : null}
      </div>
      <div className="lesson-upload-alert" role="alert">
        {state.phase === "failed" ? <>
          <p><strong>{state.message}</strong></p>
          {state.detail ? <p className="lesson-upload-detail">{state.detail}</p> : null}
        </> : null}
      </div>
      {stageVisible && ["hashing", "creating", "uploading", "confirming"].includes(state.phase)
        ? <progress aria-label="Andamento do envio" max="100" value={state.progress}>{state.progress}%</progress>
        : null}
      {stageVisible ? <div className="lesson-upload-actions">
        <button className="secondary-button" type="button" onClick={() => void cancelUpload()}>{JOURNEY_COPY.stopUpload}</button>
        {state.phase === "delayed" ? <button className="secondary-button" type="button" onClick={retryVerification}>Conferir de novo</button> : null}
      </div> : null}
      {state.phase === "failed" && !loaded ? <button className="secondary-button" type="button" onClick={() => void load()}>Tentar de novo</button> : null}
    </div>

    {children}

    {!archived && loaded ? <div className={`lesson-picker${firstChoice ? " is-first" : ""}`} hidden={uploadBusy}>
      {firstChoice ? <span className="lesson-picker-icon" aria-hidden="true">▶</span> : null}
      <div className="lesson-picker-control">
        <label className={firstChoice ? "primary-button" : "secondary-button"} htmlFor={`asset-file-${panelId}`} onClick={() => { replacementTargetRef.current = null; }}>
          {state.phase === "failed" || state.phase === "cancelled" ? JOURNEY_COPY.tryAgain : pickLabel}
        </label>
        <input ref={inputRef} id={`asset-file-${panelId}`} type="file" accept={acceptForContentRole(contentRole)} onClick={resetReplacementOnDirectPickerClick} onChange={(event) => void selectFile(event)} disabled={busy}/>
      </div>
      {!attachments ? <p className="lesson-picker-help">{JOURNEY_COPY.mainLimits}</p> : null}
    </div> : null}
  </div>;
}

/** A fase em palavras, sem a porcentagem: é o que o leitor de tela anuncia. */
function spokenStage(phase: Phase): string {
  return phase === "verifying" || phase === "delayed" ? MATERIAL_PREPARATION_MESSAGE : "Enviando o arquivo";
}

function fileBadge(mediaType: unknown): string {
  const spec = assetMediaSpec(mediaType);
  if (!spec) return "ARQ";
  return spec.extension === "mp4" ? "▶" : spec.extension.toUpperCase();
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** Por que o envio não começou, dito para quem vende, com o que fazer. */
function startFailure(error: unknown): [string, string] {
  const code = error instanceof Error ? error.message : "";
  if (code === "intent_409") {
    return ["O curso mudou em outra aba ou aparelho.", "Recarregue a página e escolha o arquivo de novo."];
  }
  if (code === "intent_401" || code === "intent_403") {
    return ["Sua sessão terminou.", "Entre de novo e escolha o arquivo outra vez."];
  }
  return ["Não deu para preparar o envio.", "Confira a internet e escolha o arquivo de novo."];
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

function selectedMediaType(file: File): AssetMediaType | null {
  return assetMediaTypeForFile(file.name, file.type);
}

/**
 * A assinatura de uma parte, conferida com a mesma régua da intenção única.
 *
 * O servidor já saneou, e esta é a segunda barreira: o navegador não manda
 * bytes para um endereço que não seja o do armazenamento, assinado e sem
 * validade longa. Duas barreiras porque o custo de errar aqui é o arquivo do
 * vendedor sair para fora.
 */
export function validPart(
  value: UploadPartPayload,
  appOrigin = "",
  nowMs = Date.now()
): boolean {
  if (!value || !Number.isInteger(value.part_number) || value.part_number < 1
    || typeof value.upload_url !== "string" || typeof value.upload_origin !== "string"
    || !Number.isInteger(value.required_content_length) || value.required_content_length < 1) return false;
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
  return headers.length === ALLOWED_PART_HEADERS.size
    && [...ALLOWED_PART_HEADERS].every((name) => headers.includes(name));
}

export function validIntent(
  value: UploadIntentPayload,
  appOrigin = "",
  nowMs = Date.now()
): boolean {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  if (!value || !uuid.test(value.upload_id) || !uuid.test(value.asset_id)) return false;
  if (value.multipart) {
    // Uma intenção fatiada não tem endereço para conferir: cada parte traz o
    // dela, e é lá que a prova acontece. Aceitar as duas coisas na mesma
    // resposta deixaria uma intenção sem URL passar como se tivesse sido
    // conferida.
    const expiresMs = Date.parse(value.expires_at);
    return value.upload_url === undefined
      && Number.isInteger(value.multipart.part_bytes) && value.multipart.part_bytes > 0
      && Number.isInteger(value.multipart.part_count) && value.multipart.part_count >= 2
      && Number.isFinite(expiresMs) && expiresMs > nowMs && expiresMs <= nowMs + MAX_MULTIPART_WINDOW_MS;
  }
  if (typeof value.upload_url !== "string" || typeof value.upload_origin !== "string") return false;
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
  return Math.max(1, Math.min(10_000, VERIFICATION_GIVE_UP_MS - Math.max(0, elapsedMs)));
}

/** Intervalo até a próxima pergunta: rápido no começo, mais espaçado depois dos 15 s. */
export function nextVerificationPollMs(elapsedMs: number, attempt: number): number {
  if (elapsedMs < MAX_VERIFICATION_WAIT_MS) {
    return Math.min(5_000, [1_500, 2_000, 4_000, 5_000][attempt] ?? 5_000);
  }
  const passos = Math.floor((elapsedMs - MAX_VERIFICATION_WAIT_MS) / 60_000);
  return DELAYED_POLL_MS[Math.min(passos, DELAYED_POLL_MS.length - 1)];
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
