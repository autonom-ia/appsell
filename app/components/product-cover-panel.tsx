"use client";

import { useEffect, useRef, useState } from "react";

import { productArt } from "@/lib/product-art";
import {
  coverAcceptAttribute,
  coverDigestHex,
  coverRejectionMessage,
  coverSizeLabel,
  describeCoverFile
} from "@/lib/product-cover";

import { AccessibleDialog } from "./accessible-dialog";

export interface ProductCover {
  url: string;
  media_type: string;
  width: number;
  height: number;
}

interface CoverCsrf {
  intent: string;
  confirm: string;
  clear: string;
}

type CoverState =
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
  | "error";

const INTENT_TIMEOUT_MS = 12_000;
const UPLOAD_TIMEOUT_MS = 60_000;
// A confirmação faz o download, a verificação e a promoção antes de responder,
// então ela é legitimamente mais lenta do que qualquer outra mutação da tela.
const CONFIRM_TIMEOUT_MS = 45_000;

interface CoverIntent {
  upload_id: string;
  upload_url: string;
  required_headers: Record<string, string>;
  required_content_length: number;
  product_version: number;
}

/**
 * Uma chave de idempotência vale para o estado em que ela nasceu.
 *
 * Repetir só é repetir a mesma coisa. Se a versão do produto mudou entre uma
 * tentativa falha e a seguinte — porque o vendedor salvou o formulário, ou
 * trocou a capa —, a operação já não é a mesma, e reaproveitar a chave faria o
 * servidor responder pela antiga enquanto a tela relata a nova.
 */
export function nextClearKey(
  current: { key: string; productVersion: number } | null,
  productVersion: number,
  mint: () => string
): { key: string; productVersion: number } {
  if (current && current.productVersion === productVersion) return current;
  return { key: mint(), productVersion };
}

/** O intent chega da nossa própria rota, mas nada obriga o contrato a bater. */
export function readCoverIntent(value: unknown): CoverIntent | null {
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
    !Number.isSafeInteger(record.required_content_length) ||
    !Number.isSafeInteger(record.product_version)
  ) return null;
  return {
    upload_id: record.upload_id,
    upload_url: record.upload_url,
    required_headers: headers as Record<string, string>,
    required_content_length: Number(record.required_content_length),
    product_version: Number(record.product_version)
  };
}

export function failureState(status: number): CoverState {
  if (status === 401 || status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 413 || status === 422) return "invalid";
  if (status === 503) return "unavailable";
  return "error";
}

/**
 * A capa do produto, do lado do vendedor.
 *
 * Os bytes nunca passam pela nossa API: o navegador pede um endereço assinado,
 * manda o arquivo direto para o armazenamento e só então avisa que ele chegou.
 * São três passos, e qualquer um pode falhar — por isso o painel diz em qual
 * deles parou, em vez de um "não foi possível" que não ajuda ninguém.
 */
export function ProductCoverPanel({
  sellerId,
  productId,
  productTitle,
  cover,
  productVersion,
  archived,
  csrf,
  onBusyChange,
  onCommitted
}: {
  sellerId: string;
  productId: string;
  productTitle: string;
  cover: ProductCover | null;
  productVersion: number;
  archived: boolean;
  csrf: CoverCsrf;
  onBusyChange?: (busy: boolean) => void;
  onCommitted: () => Promise<boolean>;
}) {
  const [state, setState] = useState<CoverState>("idle");
  const [rejection, setRejection] = useState("");
  const [removing, setRemoving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const removeCancel = useRef<HTMLButtonElement>(null);
  // O painel pode desmontar no meio de um envio — o vendedor troca de área e o
  // React tira a árvore inteira. Sem isto, um `setState` tardio some sem falar
  // com ninguém, e a falha nunca chega aos olhos de quem enviou.
  const mounted = useRef(true);
  const inFlight = useRef(new Set<AbortController>());
  const clearKey = useRef<{ key: string; productVersion: number } | null>(null);

  const base = `/api/sellers/${encodeURIComponent(sellerId)}/products/${encodeURIComponent(productId)}/cover`;
  const busy = state === "hashing" || state === "uploading" || state === "verifying";
  const art = productArt(productTitle);

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

  function settle(next: CoverState) {
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
      // O servidor assinou um tamanho diferente do que este arquivo tem. Mandar
      // assim mesmo seria um PUT que o armazenamento recusa sem explicação.
      settle("error");
      return;
    }
    if (!(await sendBytes(intent, bytes))) return;
    await confirm(intent);
  }

  async function requestIntent(
    mediaType: string,
    byteSize: number,
    sha256: string
  ): Promise<CoverIntent | null> {
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
        const intent = readCoverIntent(await response.json());
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

  async function sendBytes(intent: CoverIntent, bytes: ArrayBuffer): Promise<boolean> {
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

  async function confirm(intent: CoverIntent) {
    const data = new FormData();
    data.set("_csrf", csrf.confirm);
    data.set("idempotency_key", crypto.randomUUID());
    data.set("expected_product_version", String(intent.product_version));
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
      settle("unavailable");
      return;
    }
    if (!saved) return;
    if (fileInput.current) fileInput.current.value = "";
    await finish();
  }

  async function removeCover() {
    if (busy) return;
    clearKey.current = nextClearKey(clearKey.current, productVersion, () => crypto.randomUUID());
    setState("verifying");
    const data = new FormData();
    data.set("_csrf", csrf.clear);
    data.set("idempotency_key", clearKey.current.key);
    data.set("expected_product_version", String(productVersion));
    let cleared = false;
    try {
      cleared = await guarded(CONFIRM_TIMEOUT_MS, async (signal) => {
        const response = await fetch(`${base}/clear`, { method: "POST", body: data, signal });
        if (!response.ok) {
          const failure = failureState(response.status);
          // Um pedido recusado nunca será aceito como repetição, então a chave vai.
          if (failure === "forbidden" || failure === "invalid") clearKey.current = null;
          settle(failure);
          return false;
        }
        return true;
      });
    } catch {
      // A chave fica de propósito: a próxima tentativa repete, não duplica.
      settle("unavailable");
      return;
    }
    if (!cleared) return;
    clearKey.current = null;
    // Só fecha depois do sucesso. Fechar antes faria a tela dizer que a capa
    // saiu enquanto ela continua no ar.
    if (mounted.current) setRemoving(false);
    await finish();
  }

  /**
   * Fecha uma mutação bem-sucedida.
   *
   * Continua "ocupado" até a releitura terminar. Se soltasse antes, o botão de
   * remover voltaria a valer com a versão antiga do produto em mãos, e o
   * servidor recusaria por conflito uma ação que o próprio vendedor acabou de
   * fazer nesta mesma aba.
   */
  async function finish() {
    const reloaded = await onCommitted();
    settle(reloaded ? "idle" : "saved_stale");
  }

  return (
    <section className="cover-panel" aria-labelledby="cover-heading">
      <h3 id="cover-heading">Capa do produto</h3>
      <p className="form-note">
        JPEG, PNG ou WebP, até 5 MB. Sem capa, o catálogo usa uma arte gerada a partir do nome.
      </p>
      <div className="cover-preview">
        {cover
          // eslint-disable-next-line @next/next/no-img-element
          ? <img className="cover-image" src={cover.url} alt={`Capa atual de ${productTitle}`} width={cover.width} height={cover.height} decoding="async" />
          : (
            <div className="cover-fallback" aria-hidden="true" style={{ background: art.gradient }}>
              <span>{art.initials}</span>
            </div>
          )}
        {cover ? null : <p className="cover-fallback-note">Arte gerada a partir do nome do produto.</p>}
      </div>
      <div className="cover-actions">
        <label className="secondary-button cover-choose">
          {cover ? "Trocar capa" : "Escolher capa"}
          <input
            ref={fileInput}
            type="file"
            accept={coverAcceptAttribute()}
            disabled={busy || archived}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              if (file) void choose(file);
            }}
          />
        </label>
        {cover ? (
          <button type="button" className="secondary-button" disabled={busy || archived} onClick={() => setRemoving(true)}>
            Remover capa
          </button>
        ) : null}
      </div>
      <div className="form-feedback" aria-live="polite" aria-atomic="true">
        {state === "hashing" ? <p className="form-note">Preparando o arquivo…</p> : null}
        {state === "uploading" ? <p className="form-note">Enviando a imagem…</p> : null}
        {state === "verifying" ? <p className="form-note">Verificando a imagem…</p> : null}
        {state === "saved_stale" ? <p className="form-note">A capa foi salva, mas não consegui recarregar a tela. Atualize a página para vê-la.</p> : null}
        {state === "invalid" ? <p className="form-error">{rejection}</p> : null}
        {state === "rejected" ? <p className="form-error">Este arquivo não passou na verificação. Escolha outra imagem.</p> : null}
        {state === "conflict" ? <p className="form-error">O produto mudou em outra aba. Recarregue antes de tentar de novo.</p> : null}
        {state === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente antes de tentar.</p> : null}
        {state === "unavailable" ? <p className="form-note">O envio não terminou. Tente novamente; nada foi alterado.</p> : null}
        {state === "error" ? <p className="form-error">Não foi possível concluir. Tente novamente.</p> : null}
      </div>
      {removing ? (
        <AccessibleDialog
          titleId="cover-remove-title"
          descriptionId="cover-remove-body"
          onClose={() => { if (!busy) setRemoving(false); }}
          initialFocusRef={removeCancel}
        >
          <h4 id="cover-remove-title">Remover a capa?</h4>
          <p id="cover-remove-body">O catálogo volta a usar a arte gerada a partir do nome. Nada mais muda.</p>
          <div className="dialog-actions">
            <button ref={removeCancel} type="button" className="secondary-button" disabled={busy} onClick={() => setRemoving(false)}>Manter capa</button>
            <button type="button" className="primary-button" disabled={busy} onClick={() => void removeCover()}>{busy ? "Removendo…" : "Remover capa"}</button>
          </div>
        </AccessibleDialog>
      ) : null}
    </section>
  );
}
