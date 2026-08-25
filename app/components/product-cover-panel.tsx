"use client";

import { useRef, useState } from "react";

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
  onCommitted
}: {
  sellerId: string;
  productId: string;
  productTitle: string;
  cover: ProductCover | null;
  productVersion: number;
  archived: boolean;
  csrf: CoverCsrf;
  onCommitted: (productVersion: number) => void;
}) {
  const [state, setState] = useState<CoverState>("idle");
  const [rejection, setRejection] = useState("");
  const [removing, setRemoving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const removeCancel = useRef<HTMLButtonElement>(null);
  // Uma chave por tentativa pendente. Uma retentativa depois de conexão caída
  // repete a mesma operação em vez de somar uma segunda.
  const clearKey = useRef<string | null>(null);

  const base = `/api/sellers/${encodeURIComponent(sellerId)}/products/${encodeURIComponent(productId)}/cover`;
  const busy = state === "hashing" || state === "uploading" || state === "verifying";
  const art = productArt(productTitle);

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
      setState("error");
      return;
    }

    const intent = await requestIntent(choice.mediaType, choice.byteSize, sha256);
    if (!intent) return;
    if (intent.required_content_length !== choice.byteSize) {
      // O servidor assinou um tamanho diferente do que este arquivo tem. Mandar
      // assim mesmo seria um PUT que o armazenamento recusa sem explicação.
      setState("error");
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
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), INTENT_TIMEOUT_MS);
    try {
      const response = await fetch(`${base}/upload-intents`, {
        method: "POST",
        body: data,
        signal: controller.signal
      });
      if (!response.ok) {
        setState(failureState(response.status));
        return null;
      }
      const payload = await response.json() as CoverIntent;
      setState("uploading");
      return payload;
    } catch {
      setState("unavailable");
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function sendBytes(intent: CoverIntent, bytes: ArrayBuffer): Promise<boolean> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
    try {
      const response = await fetch(intent.upload_url, {
        method: "PUT",
        headers: intent.required_headers,
        body: bytes,
        signal: controller.signal
      });
      if (!response.ok) {
        setState("unavailable");
        return false;
      }
      setState("verifying");
      return true;
    } catch {
      setState("unavailable");
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function confirm(intent: CoverIntent) {
    const data = new FormData();
    data.set("_csrf", csrf.confirm);
    data.set("idempotency_key", crypto.randomUUID());
    data.set("expected_product_version", String(intent.product_version));
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), CONFIRM_TIMEOUT_MS);
    try {
      const response = await fetch(`${base}/uploads/${encodeURIComponent(intent.upload_id)}/confirm`, {
        method: "POST",
        body: data,
        signal: controller.signal
      });
      if (!response.ok) {
        setState(response.status === 422 ? "rejected" : failureState(response.status));
        return;
      }
      const payload = await response.json() as { product_version?: number };
      setState("idle");
      if (fileInput.current) fileInput.current.value = "";
      onCommitted(payload.product_version ?? intent.product_version + 1);
    } catch {
      setState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  async function removeCover() {
    if (busy) return;
    clearKey.current ??= crypto.randomUUID();
    setState("verifying");
    const data = new FormData();
    data.set("_csrf", csrf.clear);
    data.set("idempotency_key", clearKey.current);
    data.set("expected_product_version", String(productVersion));
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), CONFIRM_TIMEOUT_MS);
    try {
      const response = await fetch(`${base}/clear`, {
        method: "POST",
        body: data,
        signal: controller.signal
      });
      if (!response.ok) {
        const failure = failureState(response.status);
        // Um pedido recusado nunca será aceito como repetição, então a chave vai.
        if (failure === "forbidden" || failure === "invalid") clearKey.current = null;
        setState(failure);
        return;
      }
      const payload = await response.json() as { product_version?: number };
      clearKey.current = null;
      // Só fecha depois do sucesso. Fechar antes faria a tela dizer que a capa
      // saiu enquanto ela continua no ar.
      setRemoving(false);
      setState("idle");
      onCommitted(payload.product_version ?? productVersion + 1);
    } catch {
      // A chave fica de propósito: a próxima tentativa repete, não duplica.
      setState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
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

export function failureState(status: number): CoverState {
  if (status === 401 || status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 413 || status === 422) return "invalid";
  if (status === 503) return "unavailable";
  return "error";
}
