"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AccessibleDialog } from "./accessible-dialog";
import { useShellProfile } from "./shell-profile-context";
import {
  accountExportFilename,
  parseAccountDeletionPayload,
  parseAccountStatusPayload
} from "../../lib/account-status";
import { parseBuyerProductsPayload, type BuyerProductItem } from "../../lib/buyer-products";

// Minha conta (#351). A identidade vem do shell — a mesma consulta que monta
// o menu, sem pedir de novo. O que esta tela busca por conta própria são os
// acessos (o mesmo contrato da biblioteca) e o status do pedido de exclusão,
// que vive no banco e não em estado local: recarregar a página não pode
// fazer o pedido "sumir".

interface AccountWorkspaceProps {
  csrfExport: string;
  csrfDeletion: string;
  passwordRecoveryHref: string | null;
}

type Loadable<T> =
  | { status: "loading" }
  | { status: "ready"; value: T }
  | { status: "failed"; message: string };

interface ActionState {
  busy: boolean;
  message: string;
  error: boolean;
}

const CONTACT_EMAIL = "contato@autonomia.site";
const REQUEST_TIMEOUT_MS = 8_000;
// A exportação é o único pedido lento: o banco monta o documento (até 15 s
// de statement_timeout) e o BFF espera 20 s. O navegador precisa esperar
// mais que os dois, senão desiste com a resposta a caminho.
const EXPORT_TIMEOUT_MS = 30_000;
const IDLE: ActionState = { busy: false, message: "", error: false };

function messageForStatus(status: number, fallback: string) {
  if (status === 401) return "Sua sessão expirou. Entre novamente.";
  if (status === 403) return "Esta ação não está disponível para esta conta.";
  if (status === 413) return `Sua conta tem mais dados do que conseguimos exportar de uma vez. Escreva para ${CONTACT_EMAIL} e combinamos a entrega.`;
  if (status === 429) return "Você já fez isso há pouco. Aguarde alguns minutos e tente de novo.";
  if (status === 503) return "O serviço não respondeu. Tente novamente.";
  return fallback;
}

function formatInstant(iso: string) {
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "long", timeStyle: "short" }).format(
    new Date(iso)
  );
}

// O nome vem do servidor (content-disposition); o fallback só existe para o
// download não ficar sem nome se um proxy no meio descartar o cabeçalho.
function filenameFromDisposition(header: string | null) {
  const match = header?.match(/filename="([^"]+)"/);
  return match?.[1] ?? accountExportFilename(new Date());
}

async function fetchWithTimeout(input: string, init: RequestInit = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, cache: "no-store", credentials: "same-origin", signal: controller.signal });
  } finally {
    window.clearTimeout(timeout);
  }
}

export function AccountWorkspace({ csrfExport, csrfDeletion, passwordRecoveryHref }: AccountWorkspaceProps) {
  const profile = useShellProfile();
  const [accesses, setAccesses] = useState<Loadable<BuyerProductItem[]>>({ status: "loading" });
  const [deletionRequestedAt, setDeletionRequestedAt] = useState<Loadable<string | null>>({ status: "loading" });
  const [exportState, setExportState] = useState<ActionState>(IDLE);
  const [deletionState, setDeletionState] = useState<ActionState>(IDLE);
  const [confirming, setConfirming] = useState(false);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const deletionTrigger = useRef<HTMLButtonElement>(null);

  const loadAccesses = useCallback(async () => {
    try {
      const response = await fetchWithTimeout("/api/me/products");
      if (!response.ok) {
        setAccesses({ status: "failed", message: messageForStatus(response.status, "Não foi possível listar seus acessos.") });
        return;
      }
      const payload = parseBuyerProductsPayload(await response.json());
      if (!payload) {
        setAccesses({ status: "failed", message: "Não foi possível validar a lista de acessos." });
        return;
      }
      setAccesses({ status: "ready", value: payload.items });
    } catch {
      setAccesses({ status: "failed", message: "A lista de acessos não respondeu. Tente novamente." });
    }
  }, []);

  const loadDeletionStatus = useCallback(async () => {
    try {
      const response = await fetchWithTimeout("/api/me/account");
      if (!response.ok) {
        setDeletionRequestedAt({ status: "failed", message: messageForStatus(response.status, "Não foi possível consultar sua conta.") });
        return;
      }
      const status = parseAccountStatusPayload(await response.json());
      if (!status) {
        setDeletionRequestedAt({ status: "failed", message: "Não foi possível validar o status da conta." });
        return;
      }
      setDeletionRequestedAt({ status: "ready", value: status.deletionRequestedAt });
    } catch {
      setDeletionRequestedAt({ status: "failed", message: "O status da conta não respondeu. Tente novamente." });
    }
  }, []);

  useEffect(() => {
    const start = window.setTimeout(() => {
      void loadAccesses();
      void loadDeletionStatus();
    }, 0);
    return () => window.clearTimeout(start);
  }, [loadAccesses, loadDeletionStatus]);

  async function exportData() {
    if (exportState.busy) return;
    setExportState({ busy: true, message: "", error: false });
    try {
      const response = await fetchWithTimeout("/api/me/account/export", {
        method: "POST",
        body: new URLSearchParams({ _csrf: csrfExport })
      }, EXPORT_TIMEOUT_MS);
      if (!response.ok) {
        setExportState({ busy: false, error: true, message: messageForStatus(response.status, "Não foi possível gerar o arquivo.") });
        return;
      }
      // Um form nativo mandaria Origin "null" e cairia na checagem de origem;
      // por isso o arquivo chega por fetch e é entregue ao navegador daqui.
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filenameFromDisposition(response.headers.get("content-disposition"));
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setExportState({ busy: false, error: false, message: "Arquivo gerado. O download começou." });
    } catch {
      setExportState({ busy: false, error: true, message: "Não foi possível gerar o arquivo. Tente novamente." });
    }
  }

  function openConfirmation() {
    setDeletionState(IDLE);
    setConfirming(true);
  }

  function closeConfirmation() {
    if (deletionState.busy) return;
    setConfirming(false);
    queueMicrotask(() => deletionTrigger.current?.focus());
  }

  async function requestDeletion() {
    if (deletionState.busy) return;
    setDeletionState({ busy: true, message: "", error: false });
    try {
      const response = await fetchWithTimeout("/api/me/account/deletion-request", {
        method: "POST",
        body: new URLSearchParams({ _csrf: csrfDeletion })
      });
      if (!response.ok) {
        setDeletionState({ busy: false, error: true, message: messageForStatus(response.status, "Não foi possível registrar o pedido.") });
        return;
      }
      const receipt = parseAccountDeletionPayload(await response.json());
      if (!receipt) {
        setDeletionState({ busy: false, error: true, message: "Não foi possível validar a resposta. Recarregue a página." });
        return;
      }
      setDeletionRequestedAt({ status: "ready", value: receipt.requestedAt });
      setDeletionState(IDLE);
      setConfirming(false);
    } catch {
      setDeletionState({ busy: false, error: true, message: "Não foi possível registrar o pedido. Tente novamente." });
    }
  }

  if (profile.status === "loading") {
    return <section className="account-card" aria-busy="true"><p className="form-note">Carregando sua conta…</p></section>;
  }
  if (profile.status === "failed" || (!profile.profile && !profile.identity)) {
    return (
      <section className="account-card" role="alert">
        <p className="eyebrow">Minha conta</p>
        <h2>Não foi possível validar seu acesso</h2>
        <p>Use &quot;Tentar novamente&quot; no menu ou entre de novo.</p>
      </section>
    );
  }

  const demo = profile.profile;
  const identity = profile.identity;
  const email = demo ? demo.email : identity?.email ?? "";
  const displayName = demo ? demo.label : identity?.displayName ?? null;
  const emailVerified = demo ? true : identity?.emailVerified === true;
  const stores = identity?.sellers ?? [];

  return (
    <div className="account-grid">
      <section className="account-card" aria-labelledby="conta-identidade">
        <p className="eyebrow">Identidade</p>
        <h2 id="conta-identidade">{displayName ?? "Sua conta"}</h2>
        <dl className="account-facts">
          <div><dt>E-mail</dt><dd>{email}</dd></div>
          <div>
            <dt>Status</dt>
            <dd>
              <span className={emailVerified ? "payout-badge payout-badge-active" : "payout-badge payout-badge-action_required"}>
                {emailVerified ? "E-mail verificado" : "E-mail não verificado"}
              </span>
            </dd>
          </div>
          <div>
            <dt>Lojas</dt>
            <dd>
              {stores.length === 0
                ? "Nenhuma. Esta conta é de aluno."
                : stores.map((store) => `${store.displayName} (${store.role.replace("seller_", "")})`).join(", ")}
            </dd>
          </div>
        </dl>
        {demo ? <p className="form-note">Ambiente demo: exportação e exclusão ficam desativadas aqui.</p> : null}
      </section>

      <section className="account-card" aria-labelledby="conta-seguranca">
        <p className="eyebrow">Segurança</p>
        <h2 id="conta-seguranca">Senha e acesso</h2>
        <p>
          A senha e o segundo fator ficam no login da Autonom.ia, não aqui. O Sell
          só reconhece quem o login já confirmou.
        </p>
        {passwordRecoveryHref
          ? <a className="secondary-button" href={passwordRecoveryHref}>Redefinir minha senha</a>
          : <p className="form-note">A troca de senha fica disponível no ambiente com o login real da Autonom.ia.</p>}
        <p className="form-note">
          Passkeys e segundo fator ainda não têm uma tela de gerenciamento. Se precisar
          mexer neles, escreva para {CONTACT_EMAIL}.
        </p>
      </section>

      <section className="account-card" aria-labelledby="conta-acessos" aria-busy={accesses.status === "loading"}>
        <p className="eyebrow">Meus acessos</p>
        <h2 id="conta-acessos">O que esta conta pode abrir</h2>
        {accesses.status === "loading" ? <p className="form-note">Carregando seus acessos…</p> : null}
        {accesses.status === "failed" ? <p className="form-error" role="alert">{accesses.message}</p> : null}
        {accesses.status === "ready" && accesses.value.length === 0
          ? <p className="form-note">Nenhum acesso ativo nesta conta.</p>
          : null}
        {accesses.status === "ready" && accesses.value.length > 0 ? (
          <ul className="account-access-list">
            {accesses.value.map((item) => (
              <li key={item.entitlement_id}>
                <strong>{item.product.title}</strong>
                <small>{item.seller.name}</small>
              </li>
            ))}
          </ul>
        ) : null}
        <Link className="secondary-button" href="/library">Abrir minha biblioteca</Link>
      </section>

      <section className="account-card" aria-labelledby="conta-dados">
        <p className="eyebrow">Meus dados</p>
        <h2 id="conta-dados">Levar ou apagar</h2>
        <p>
          A exportação traz o que sabemos sobre você: identidade, acessos, compras,
          progresso, marcadores e anotações (com os traços que você desenhou), os
          e-mails que enviamos e o seu histórico de ações. Sem senhas, sem tokens,
          sem dados de outras pessoas.
        </p>
        <div className="account-actions">
          <button type="button" className="primary-button" onClick={() => void exportData()} disabled={exportState.busy || Boolean(demo)}>
            {exportState.busy ? "Gerando arquivo…" : "Exportar meus dados"}
          </button>
        </div>
        {exportState.message
          ? <p className={exportState.error ? "form-error" : "form-success"} role={exportState.error ? "alert" : "status"}>{exportState.message}</p>
          : null}

        {deletionRequestedAt.status === "ready" && deletionRequestedAt.value ? (
          <p className="form-success" role="status">
            Pedido de exclusão registrado em {formatInstant(deletionRequestedAt.value)}. A
            exclusão é feita pela equipe, respeitando os prazos legais de guarda dos
            registros de compra. Responderemos pelo e-mail desta conta.
          </p>
        ) : (
          <>
            <p className="form-note">
              Pedir a exclusão registra o pedido com data e hora. A conta não some na
              hora: os registros de compra têm prazo legal de guarda, e a equipe
              executa a exclusão do que pode ser apagado.
            </p>
            {deletionRequestedAt.status === "failed" ? <p className="form-error" role="alert">{deletionRequestedAt.message}</p> : null}
            <div className="account-actions">
              <button
                ref={deletionTrigger}
                type="button"
                className="danger-button"
                onClick={openConfirmation}
                disabled={deletionRequestedAt.status !== "ready" || Boolean(demo)}
              >
                Pedir exclusão da conta
              </button>
            </div>
          </>
        )}
      </section>

      {confirming ? (
        <AccessibleDialog titleId="exclusao-titulo" descriptionId="exclusao-descricao" initialFocusRef={cancelButton} returnFocusRef={deletionTrigger} onClose={closeConfirmation}>
          <p className="eyebrow">Pedido de exclusão</p>
          <h2 id="exclusao-titulo">Registrar o pedido de exclusão?</h2>
          <p id="exclusao-descricao">
            Vamos registrar agora, com data e hora, que você pediu a exclusão desta
            conta. Seus acessos continuam funcionando até a equipe concluir. Você pode
            exportar seus dados antes.
          </p>
          {deletionState.message ? <p className="form-error" role="alert">{deletionState.message}</p> : null}
          <div className="dialog-actions">
            <button ref={cancelButton} type="button" className="secondary-button" onClick={closeConfirmation} disabled={deletionState.busy}>Voltar</button>
            <button type="button" className="danger-button" onClick={() => void requestDeletion()} disabled={deletionState.busy}>
              {deletionState.busy ? "Registrando…" : "Confirmar pedido"}
            </button>
          </div>
        </AccessibleDialog>
      ) : null}
    </div>
  );
}
