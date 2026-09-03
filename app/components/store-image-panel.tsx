"use client";

import { useEffect, useRef, useState } from "react";

import {
  coverAcceptAttribute,
  coverDigestHex,
  coverRejectionMessage,
  coverSizeLabel,
  describeCoverFile
} from "@/lib/product-cover";
import { storeInitials } from "@/lib/store-brand";
import type { StoreImageKind, StoreLogo } from "@/lib/store-settings";

import { AccessibleDialog } from "./accessible-dialog";
import { failureState } from "./product-image-panel";

// O logo e o banner da loja, do lado do vendedor (#352, #387). O mesmo
// caminho da capa do produto (endereço assinado, envio direto, confirmação)
// com a loja como dona. Sem versão de produto para carregar: a loja não tem
// trava otimista. O tipo vai na rota; o resto é igual.

const COPY: Record<StoreImageKind, {
  title: string;
  hint: string;
  choose: string;
  replace: string;
  remove: string;
  removeTitle: string;
  removeBody: string;
  keep: string;
  fallbackNote: string;
  alt: string;
  stale: string;
}> = {
  logo: {
    title: "Logo da loja",
    hint: "JPEG, PNG ou WebP, até 5 MB. Quadrado ou horizontal, fundo transparente ou claro. Sem logo, a loja mostra as iniciais do nome.",
    choose: "Escolher logo",
    replace: "Trocar logo",
    remove: "Remover logo",
    removeTitle: "Remover o logo?",
    removeBody: "A loja volta a mostrar as iniciais do nome. Nada mais muda.",
    keep: "Manter logo",
    fallbackNote: "Iniciais geradas a partir do nome da loja.",
    alt: "Logo atual",
    stale: "O logo foi salvo, mas não consegui recarregar a tela. Atualize a página para vê-lo."
  },
  banner: {
    title: "Banner da loja",
    hint: "A capa larga no alto da sua loja. JPEG, PNG ou WebP, até 5 MB; o ideal é 1600 por 500, com o que importa no centro: no celular as laterais são cortadas.",
    choose: "Escolher banner",
    replace: "Trocar banner",
    remove: "Remover banner",
    removeTitle: "Remover o banner?",
    removeBody: "A loja volta a abrir sem capa, só com o logo e o nome. Nada mais muda.",
    keep: "Manter banner",
    fallbackNote: "Sem banner, a loja abre só com o logo e o nome.",
    alt: "Banner atual",
    stale: "O banner foi salvo, mas não consegui recarregar a tela. Atualize a página para vê-lo."
  }
};

interface LogoCsrf {
  intent: string;
  confirm: string;
  clear: string;
}

type LogoState =
  | "idle"
  | "hashing"
  | "uploading"
  | "verifying"
  | "saved_stale"
  | "invalid"
  | "rejected"
  | "conflict"
  | "forbidden"
  | "unavailable"
  | "unconfirmed"
  | "unconfirmed_stale"
  | "error";

const INTENT_TIMEOUT_MS = 12_000;
const UPLOAD_TIMEOUT_MS = 60_000;
const CONFIRM_TIMEOUT_MS = 45_000;

interface LogoIntent {
  upload_id: string;
  upload_url: string;
  required_headers: Record<string, string>;
  required_content_length: number;
}

export function readLogoIntent(value: unknown): LogoIntent | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const headers = record.required_headers;
  if (
    typeof record.upload_id !== "string" ||
    typeof record.upload_url !== "string" ||
    !record.upload_id ||
    !record.upload_url ||
    typeof headers !== "object" ||
    headers === null ||
    Array.isArray(headers) ||
    Object.values(headers).some((header) => typeof header !== "string") ||
    !Number.isSafeInteger(record.required_content_length)
  ) return null;
  return {
    upload_id: record.upload_id,
    upload_url: record.upload_url,
    required_headers: headers as Record<string, string>,
    required_content_length: Number(record.required_content_length)
  };
}

export function StoreImagePanel({
  sellerId,
  storeName,
  kind,
  image: logo,
  csrf,
  onBusyChange,
  onCommitted
}: {
  sellerId: string;
  storeName: string;
  kind: StoreImageKind;
  image: StoreLogo | null;
  csrf: LogoCsrf;
  onBusyChange?: (busy: boolean) => void;
  onCommitted: () => Promise<boolean>;
}) {
  const [state, setState] = useState<LogoState>("idle");
  const [rejection, setRejection] = useState("");
  const [removing, setRemoving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const removeCancel = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);
  const inFlight = useRef(new Set<AbortController>());

  const base = `/api/sellers/${encodeURIComponent(sellerId)}/settings/${kind}`;
  const copy = COPY[kind];
  const busy = state === "hashing" || state === "uploading" || state === "verifying";

  useEffect(() => {
    mounted.current = true;
    const pending = inFlight.current;
    return () => {
      mounted.current = false;
      for (const controller of pending) controller.abort();
      pending.clear();
    };
  }, []);

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  function settle(next: LogoState) {
    if (mounted.current) setState(next);
  }

  async function guarded<T>(timeoutMs: number, run: (signal: AbortSignal) => Promise<T>) {
    const controller = new AbortController();
    inFlight.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await run(controller.signal);
    } finally {
      clearTimeout(timeout);
      inFlight.current.delete(controller);
    }
  }

  async function choose(file: File) {
    const choice = describeCoverFile(file);
    if (!choice.ok) {
      setRejection(
        choice.reason === "too_large"
          ? `${coverRejectionMessage(choice.reason)} Este tem ${coverSizeLabel(file.size)}.`
          : coverRejectionMessage(choice.reason)
      );
      setState("invalid");
      return;
    }
    setRejection("");
    setState("hashing");
    let bytes: ArrayBuffer;
    let sha256: string;
    try {
      bytes = await file.arrayBuffer();
      sha256 = await coverDigestHex(bytes);
    } catch {
      settle("error");
      return;
    }
    if (!mounted.current) return;
    const intent = await requestIntent(choice.mediaType, choice.byteSize, sha256);
    if (!intent) return;
    if (intent.required_content_length !== choice.byteSize) {
      settle("error");
      return;
    }
    if (!(await sendBytes(intent, bytes))) return;
    await confirm(intent);
  }

  async function requestIntent(mediaType: string, byteSize: number, sha256: string): Promise<LogoIntent | null> {
    const data = new FormData();
    data.set("_csrf", csrf.intent);
    data.set("idempotency_key", crypto.randomUUID());
    data.set("media_type", mediaType);
    data.set("byte_size", String(byteSize));
    data.set("sha256", sha256);
    try {
      return await guarded(INTENT_TIMEOUT_MS, async (signal) => {
        const response = await fetch(`${base}/upload-intents`, { method: "POST", body: data, signal });
        if (!response.ok) {
          settle(failureState(response.status));
          return null;
        }
        const intent = readLogoIntent(await response.json());
        if (!intent) {
          settle("error");
          return null;
        }
        settle("uploading");
        return intent;
      });
    } catch {
      settle("unavailable");
      return null;
    }
  }

  async function sendBytes(intent: LogoIntent, bytes: ArrayBuffer): Promise<boolean> {
    try {
      return await guarded(UPLOAD_TIMEOUT_MS, async (signal) => {
        const response = await fetch(intent.upload_url, {
          method: "PUT",
          headers: intent.required_headers,
          body: bytes,
          signal
        });
        if (!response.ok) {
          settle("unavailable");
          return false;
        }
        settle("verifying");
        return true;
      });
    } catch {
      settle("unavailable");
      return false;
    }
  }

  async function confirm(intent: LogoIntent) {
    const data = new FormData();
    data.set("_csrf", csrf.confirm);
    data.set("idempotency_key", crypto.randomUUID());
    let saved = false;
    try {
      saved = await guarded(CONFIRM_TIMEOUT_MS, async (signal) => {
        const response = await fetch(`${base}/uploads/${encodeURIComponent(intent.upload_id)}/confirm`, {
          method: "POST",
          body: data,
          signal
        });
        if (!response.ok) {
          settle(response.status === 422 ? "rejected" : failureState(response.status));
          return false;
        }
        return true;
      });
    } catch {
      // Pode ter chegado ao fim no servidor com a resposta perdida: a tela
      // recarrega e conta o que sabe, em vez de afirmar que nada mudou.
      let recarregou = false;
      try {
        recarregou = await onCommitted();
      } catch {
        recarregou = false;
      }
      // Sem isto, escolher o mesmo arquivo de novo não dispara evento nenhum.
      if (fileInput.current) fileInput.current.value = "";
      settle(recarregou ? "unconfirmed" : "unconfirmed_stale");
      return;
    }
    if (!saved) return;
    if (fileInput.current) fileInput.current.value = "";
    await finish();
  }

  async function removeLogo() {
    if (busy) return;
    setState("verifying");
    const data = new FormData();
    data.set("_csrf", csrf.clear);
    data.set("idempotency_key", crypto.randomUUID());
    let cleared = false;
    try {
      cleared = await guarded(CONFIRM_TIMEOUT_MS, async (signal) => {
        const response = await fetch(`${base}/clear`, { method: "POST", body: data, signal });
        if (!response.ok) {
          settle(failureState(response.status));
          return false;
        }
        return true;
      });
    } catch {
      settle("unavailable");
      return;
    }
    if (!cleared) return;
    if (mounted.current) setRemoving(false);
    await finish();
  }

  async function finish() {
    const reloaded = await onCommitted();
    settle(reloaded ? "idle" : "saved_stale");
  }

  return (
    <section className="cover-panel" aria-labelledby={`${kind}-heading`}>
      <h3 id={`${kind}-heading`}>{copy.title}</h3>
      <p className="form-note">{copy.hint}</p>
      <div className="cover-preview">
        {logo
          // eslint-disable-next-line @next/next/no-img-element
          ? <img className={kind === "banner" ? "cover-image store-banner-preview" : "cover-image store-logo-preview"} src={logo.url} alt={`${copy.alt} de ${storeName}`} width={logo.width} height={logo.height} decoding="async" />
          : kind === "logo" ? (
            <div className="cover-fallback store-logo-fallback" aria-hidden="true">
              <span>{storeInitials(storeName)}</span>
            </div>
          ) : (
            <div className="cover-fallback store-banner-fallback" aria-hidden="true"/>
          )}
        {logo ? null : <p className="cover-fallback-note">{copy.fallbackNote}</p>}
      </div>
      <div className="cover-actions">
        <label className="secondary-button cover-choose">
          {logo ? copy.replace : copy.choose}
          <input
            ref={fileInput}
            type="file"
            accept={coverAcceptAttribute()}
            disabled={busy}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              if (file) void choose(file);
            }}
          />
        </label>
        {logo ? (
          <button type="button" className="secondary-button" disabled={busy} onClick={() => setRemoving(true)}>
            {copy.remove}
          </button>
        ) : null}
      </div>
      <div className="form-feedback" aria-live="polite" aria-atomic="true">
        {state === "hashing" ? <p className="form-note">Preparando o arquivo…</p> : null}
        {state === "uploading" ? <p className="form-note">Enviando a imagem…</p> : null}
        {state === "verifying" ? <p className="form-note">Verificando a imagem…</p> : null}
        {state === "saved_stale" ? <p className="form-note">{copy.stale}</p> : null}
        {state === "invalid" ? <p className="form-error">{rejection}</p> : null}
        {state === "rejected" ? <p className="form-error">Este arquivo não passou na verificação. Escolha outra imagem.</p> : null}
        {state === "conflict" ? <p className="form-error">Outro envio está em andamento. Aguarde e tente de novo.</p> : null}
        {state === "forbidden" ? <p className="form-error">Só o dono ou um administrador da loja pode mudar as imagens.</p> : null}
        {state === "unavailable" ? <p className="form-note">O envio não terminou. Tente novamente; nada foi alterado.</p> : null}
        {state === "unconfirmed" ? <p className="form-note">Não consegui confirmar o envio. Recarreguei a tela: se a nova imagem aparece aqui em cima, ela foi salva. Se não, envie outra vez.</p> : null}
        {state === "unconfirmed_stale" ? <p className="form-note">Não consegui confirmar o envio nem recarregar a tela. Atualize a página para ver se a imagem foi salva antes de enviar de novo.</p> : null}
        {state === "error" ? <p className="form-error">Não foi possível concluir. Tente novamente.</p> : null}
      </div>
      {removing ? (
        <AccessibleDialog
          titleId={`${kind}-remove-title`}
          descriptionId={`${kind}-remove-body`}
          onClose={() => { if (!busy) setRemoving(false); }}
          initialFocusRef={removeCancel}
        >
          <h4 id={`${kind}-remove-title`}>{copy.removeTitle}</h4>
          <p id={`${kind}-remove-body`}>{copy.removeBody}</p>
          <div className="dialog-actions">
            <button ref={removeCancel} type="button" className="secondary-button" disabled={busy} onClick={() => setRemoving(false)}>{copy.keep}</button>
            <button type="button" className="primary-button" disabled={busy} onClick={() => void removeLogo()}>{busy ? "Removendo…" : copy.remove}</button>
          </div>
        </AccessibleDialog>
      ) : null}
    </section>
  );
}
