"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { AccessibleDialog } from "./accessible-dialog";

interface AccessLink {
  access_link_id: string;
  token_prefix: string;
  label: string;
  max_uses: number | null;
  redeemed_count: number;
  expires_at: string | null;
  revoked_at: string | null;
  rotated_at: string | null;
  created_at: string | null;
}

interface Redemption {
  buyer_identity_user_id: string;
  entitlement_id: string;
  redeemed_at: string | null;
}

interface AccessLinkCsrf {
  create: string;
  revoke: string;
  rotate: string;
}

type ListState = "loading" | "ready" | "unauthorized" | "unavailable" | "error";
type ActionState = "idle" | "working" | "invalid" | "forbidden" | "unavailable" | "error";

export function AccessLinksPanel({
  sellerId,
  productId,
  csrf,
  published
}: {
  sellerId: string;
  productId: string;
  csrf: AccessLinkCsrf;
  published: boolean;
}) {
  const [links, setLinks] = useState<AccessLink[]>([]);
  const [listState, setListState] = useState<ListState>("loading");
  const [loadedAt, setLoadedAt] = useState(0);
  const [actionState, setActionState] = useState<ActionState>("idle");
  const [invalidField, setInvalidField] = useState("");
  // Shown once, right after minting. There is no way to ask for it again, so the
  // panel keeps it visible until the seller creates or rotates another link.
  const [issuedUrl, setIssuedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<AccessLink | null>(null);
  const [openRedemptions, setOpenRedemptions] = useState<string | null>(null);
  const [redemptions, setRedemptions] = useState<Redemption[]>([]);
  const listHeading = useRef<HTMLHeadingElement>(null);
  const revokeCancel = useRef<HTMLButtonElement>(null);

  const base = `/api/sellers/${encodeURIComponent(sellerId)}/products/${encodeURIComponent(productId)}/access-links`;

  const loadLinks = useCallback(async () => {
    try {
      const response = await fetch(base, { cache: "no-store" });
      if (response.status === 401) return setListState("unauthorized");
      if (response.status === 503) return setListState("unavailable");
      if (!response.ok) return setListState("error");
      const payload = await response.json() as { access_links?: AccessLink[] };
      setLinks(Array.isArray(payload.access_links) ? payload.access_links : []);
      // Captured once per load so the render stays pure: expiry is judged against
      // the moment the list came from the server, not against a clock read during
      // rendering.
      setLoadedAt(Date.now());
      setListState("ready");
    } catch {
      setListState("unavailable");
    }
  }, [base]);

  useEffect(() => {
    // Deferred by a microtask so the first render is never interrupted by the
    // state the load produces.
    void Promise.resolve().then(loadLinks);
  }, [loadLinks]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    data.set("_csrf", csrf.create);
    data.set("idempotency_key", crypto.randomUUID());
    if (!data.get("max_uses")) data.set("max_uses", "");
    if (!data.get("expires_at")) data.set("expires_at", "");
    setActionState("working");
    setInvalidField("");
    try {
      const response = await fetch(base, { method: "POST", body: data });
      if (!response.ok) return setActionState(await failureState(response, setInvalidField));
      const payload = await response.json() as { url?: string };
      setIssuedUrl(typeof payload.url === "string" ? payload.url : null);
      setCopied(false);
      form.reset();
      setActionState("idle");
      await loadLinks();
      queueMicrotask(() => listHeading.current?.focus());
    } catch {
      setActionState("unavailable");
    }
  }

  async function revoke(link: AccessLink) {
    setRevokeTarget(null);
    setActionState("working");
    const data = new FormData();
    data.set("_csrf", csrf.revoke);
    data.set("idempotency_key", crypto.randomUUID());
    try {
      const response = await fetch(`${base}/${encodeURIComponent(link.access_link_id)}/revoke`, {
        method: "POST",
        body: data
      });
      if (!response.ok) return setActionState(await failureState(response, setInvalidField));
      setActionState("idle");
      await loadLinks();
    } catch {
      setActionState("unavailable");
    }
  }

  async function rotate(link: AccessLink) {
    setActionState("working");
    const data = new FormData();
    data.set("_csrf", csrf.rotate);
    data.set("idempotency_key", crypto.randomUUID());
    try {
      const response = await fetch(`${base}/${encodeURIComponent(link.access_link_id)}/rotate`, {
        method: "POST",
        body: data
      });
      if (!response.ok) return setActionState(await failureState(response, setInvalidField));
      const payload = await response.json() as { url?: string };
      setIssuedUrl(typeof payload.url === "string" ? payload.url : null);
      setCopied(false);
      setActionState("idle");
      await loadLinks();
    } catch {
      setActionState("unavailable");
    }
  }

  async function loadRedemptions(link: AccessLink) {
    if (openRedemptions === link.access_link_id) {
      setOpenRedemptions(null);
      return;
    }
    setOpenRedemptions(link.access_link_id);
    setRedemptions([]);
    try {
      const response = await fetch(
        `${base}/${encodeURIComponent(link.access_link_id)}/redemptions`,
        { cache: "no-store" }
      );
      if (!response.ok) return;
      const payload = await response.json() as { redemptions?: Redemption[] };
      setRedemptions(Array.isArray(payload.redemptions) ? payload.redemptions : []);
    } catch {
      setRedemptions([]);
    }
  }

  async function copyIssued() {
    if (!issuedUrl) return;
    try {
      await navigator.clipboard.writeText(issuedUrl);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="access-links-panel" aria-labelledby="access-links-title">
      <p className="eyebrow">Distribuição</p>
      <h2 id="access-links-title">Links de acesso</h2>
      <p>
        Cada link libera vagas contadas por pessoa. Quem abrir entra com o próprio cadastro, e
        reabrir o link não consome outra vaga.
      </p>
      {!published ? (
        <p className="form-note" role="status">
          Coloque o produto no ar para os links começarem a funcionar. Você já pode criá-los.
        </p>
      ) : null}

      {issuedUrl ? (
        <div className="access-link-issued" role="status">
          <div>
            <strong>Copie agora. Este endereço não aparece de novo.</strong>
            <p>Guardamos apenas uma impressão digital dele, então não temos como mostrá-lo outra vez.</p>
            <code>{issuedUrl}</code>
          </div>
          <button type="button" className="primary-button" onClick={() => void copyIssued()}>
            {copied ? "Copiado" : "Copiar endereço"}
          </button>
        </div>
      ) : null}

      <form className="access-link-form" onSubmit={submit}>
        <fieldset disabled={actionState === "working"}>
          <legend className="sr-only">Novo link de acesso</legend>
          <label>
            Nome do link
            <input
              name="label"
              required
              maxLength={80}
              autoComplete="off"
              placeholder="Ex.: Turma Royalty Seguros"
              aria-invalid={invalidField === "label"}
            />
          </label>
          <label>
            Vagas
            <input
              name="max_uses"
              inputMode="numeric"
              pattern="[0-9]*"
              autoComplete="off"
              placeholder="Deixe vazio para ilimitado"
              aria-invalid={invalidField === "max_uses"}
            />
          </label>
          <label>
            Validade
            <input
              type="datetime-local"
              name="expires_at"
              autoComplete="off"
              aria-invalid={invalidField === "expires_at"}
            />
          </label>
          <button type="submit" className="secondary-button">
            {actionState === "working" ? "Criando…" : "Criar link"}
          </button>
        </fieldset>
        <div className="form-feedback" aria-live="polite" aria-atomic="true">
          {actionState === "invalid" ? (
            <p className="form-error">Revise o campo destacado e tente de novo.</p>
          ) : null}
          {actionState === "forbidden" ? (
            <p className="form-error">Sua sessão mudou. Entre novamente antes de tentar.</p>
          ) : null}
          {actionState === "unavailable" ? (
            <p className="form-note">A API não respondeu. Nada foi criado; tente novamente.</p>
          ) : null}
          {actionState === "error" ? (
            <p className="form-error">Não foi possível concluir a ação.</p>
          ) : null}
        </div>
      </form>

      <h3 ref={listHeading} tabIndex={-1}>Links criados</h3>
      {listState === "loading" ? <p className="empty-state">Carregando links…</p> : null}
      {listState === "unauthorized" ? <p className="empty-state">Sua sessão expirou.</p> : null}
      {listState === "unavailable" ? (
        <p className="empty-state">A API está indisponível. Tente novamente.</p>
      ) : null}
      {listState === "error" ? (
        <p className="empty-state">Não foi possível carregar os links.</p>
      ) : null}
      {listState === "ready" && links.length === 0 ? (
        <p className="empty-state">Nenhum link ainda. Crie o primeiro acima.</p>
      ) : null}

      {links.map((link) => {
        const remaining = link.max_uses === null
          ? null
          : Math.max(link.max_uses - link.redeemed_count, 0);
        const revoked = link.revoked_at !== null;
        const expired = link.expires_at !== null && Date.parse(link.expires_at) <= loadedAt;
        return (
          <article className="access-link-card" key={link.access_link_id}>
            <div className="access-link-heading">
              <h4>{link.label}</h4>
              <span className={`status status-${revoked || expired ? "unpublished" : "published"}`}>
                {revoked ? "Revogado" : expired ? "Vencido" : "Ativo"}
              </span>
            </div>
            <p className="access-link-usage">
              {link.max_uses === null
                ? `${link.redeemed_count} ${link.redeemed_count === 1 ? "pessoa entrou" : "pessoas entraram"} · sem limite`
                : `${link.redeemed_count} de ${link.max_uses} vagas usadas · ${remaining} ${remaining === 1 ? "restante" : "restantes"}`}
            </p>
            {link.max_uses !== null ? (
              <div
                className="access-link-seatbar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={link.max_uses}
                aria-valuenow={link.redeemed_count}
                aria-label={`Vagas usadas em ${link.label}`}
              >
                <i style={{ width: `${Math.min(100, (link.redeemed_count / link.max_uses) * 100)}%` }} />
              </div>
            ) : null}
            <p className="access-link-address">
              Começa com <code>{link.token_prefix}</code>
              {link.expires_at ? ` · vence em ${formatDate(link.expires_at)}` : ""}
            </p>
            <div className="access-link-actions">
              <button type="button" className="secondary-button" onClick={() => void loadRedemptions(link)}>
                {openRedemptions === link.access_link_id ? "Ocultar quem entrou" : "Ver quem entrou"}
              </button>
              {!revoked ? (
                <>
                  <button type="button" className="secondary-button" onClick={() => void rotate(link)}>
                    Gerar novo endereço
                  </button>
                  <button type="button" className="danger-button" onClick={() => setRevokeTarget(link)}>
                    Revogar
                  </button>
                </>
              ) : null}
            </div>
            {openRedemptions === link.access_link_id ? (
              <ul className="access-link-redemptions">
                {redemptions.length === 0 ? <li>Ninguém entrou por este link ainda.</li> : null}
                {redemptions.map((entry) => (
                  <li key={entry.entitlement_id}>
                    Entrou em {formatDate(entry.redeemed_at)}
                  </li>
                ))}
              </ul>
            ) : null}
          </article>
        );
      })}

      {revokeTarget ? (
        <AccessibleDialog
          titleId="revoke-link-title"
          descriptionId="revoke-link-description"
          initialFocusRef={revokeCancel}
          onClose={() => setRevokeTarget(null)}
        >
          <p className="eyebrow">Confirmação</p>
          <h2 id="revoke-link-title">Revogar {revokeTarget.label}?</h2>
          <p id="revoke-link-description">
            O endereço para de funcionar para quem ainda não entrou. Quem já entrou continua com
            acesso ao conteúdo.
          </p>
          <div className="dialog-actions">
            <button ref={revokeCancel} type="button" className="secondary-button" onClick={() => setRevokeTarget(null)}>
              Manter o link
            </button>
            <button type="button" className="danger-button" onClick={() => void revoke(revokeTarget)}>
              Revogar link
            </button>
          </div>
        </AccessibleDialog>
      ) : null}
    </section>
  );
}

async function failureState(
  response: Response,
  setInvalidField: (field: string) => void
): Promise<ActionState> {
  if (response.status === 401 || response.status === 403) return "forbidden";
  if (response.status === 503) return "unavailable";
  if (response.status === 422) {
    const payload = await response.json().catch(() => null) as { field?: string } | null;
    setInvalidField(payload?.field ?? "form");
    return "invalid";
  }
  return "error";
}

function formatDate(value: string | null) {
  if (!value) return "data desconhecida";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "data desconhecida";
  return new Date(parsed).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}
