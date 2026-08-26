"use client";

import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  SEAT_HELP,
  SEAT_RANGE,
  isRejectedNumericKey,
  keepDigits,
  readSeatInput,
  seatRangeMessage
} from "@/lib/numeric-field";

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

type ListState = "loading" | "ready" | "unauthorized" | "forbidden" | "unavailable" | "error";
type ActionState = "idle" | "working" | "invalid" | "forbidden" | "unavailable" | "error";

const LOAD_TIMEOUT_MS = 8_000;
const MUTATION_TIMEOUT_MS = 12_000;

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
  // panel keeps it visible until another link is created.
  const [issuedUrl, setIssuedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<AccessLink | null>(null);
  const [openRedemptions, setOpenRedemptions] = useState<string | null>(null);
  const [redemptions, setRedemptions] = useState<Redemption[]>([]);
  const [label, setLabel] = useState("");
  const [maxUses, setMaxUses] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  // Só vira verdadeiro quando a pessoa aciona o botão sem nome preenchido. O
  // formulário não abre reclamando de um campo que ninguém tentou usar ainda.
  const [missingLabel, setMissingLabel] = useState(false);

  // Derivado a cada render: o limite de vagas é conferido enquanto se digita,
  // não no envio.
  const seatMessage = seatRangeMessage(readSeatInput(maxUses));
  const createBlocked = label.trim().length === 0 || seatMessage !== null;

  const listHeading = useRef<HTMLHeadingElement>(null);
  const labelInput = useRef<HTMLInputElement>(null);
  const seatsInput = useRef<HTMLInputElement>(null);
  const revokeCancel = useRef<HTMLButtonElement>(null);
  // One key per pending mutation, held until the server confirms. A retry after a
  // dropped connection then replays the same operation instead of minting a
  // second link or spending a second seat.
  const createKey = useRef<string | null>(null);
  const mutationKeys = useRef(new Map<string, string>());
  const listRequest = useRef<{ controller: AbortController; version: number } | null>(null);
  const listVersion = useRef(0);
  const redemptionRequest = useRef<{ controller: AbortController; linkId: string } | null>(null);
  const inFlight = useRef(new Set<AbortController>());

  const base = `/api/sellers/${encodeURIComponent(sellerId)}/products/${encodeURIComponent(productId)}/access-links`;

  const loadLinks = useCallback(async () => {
    listRequest.current?.controller.abort();
    const version = ++listVersion.current;
    const controller = new AbortController();
    listRequest.current = { controller, version };
    const timeout = setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS);
    try {
      const response = await fetch(base, { cache: "no-store", signal: controller.signal });
      // A slower earlier answer must never overwrite a newer one.
      if (listVersion.current !== version) return;
      if (response.status === 401) return setListState("unauthorized");
      if (response.status === 403) return setListState("forbidden");
      if (response.status === 503) return setListState("unavailable");
      if (!response.ok) return setListState("error");
      const payload = await response.json() as { access_links?: AccessLink[] };
      if (listVersion.current !== version) return;
      setLinks(Array.isArray(payload.access_links) ? payload.access_links : []);
      // Captured per load so rendering stays pure: expiry is judged against the
      // moment the list arrived, not against a clock read during render.
      setLoadedAt(Date.now());
      setListState("ready");
    } catch {
      if (listVersion.current === version) setListState("unavailable");
    } finally {
      clearTimeout(timeout);
      if (listRequest.current?.version === version) listRequest.current = null;
    }
  }, [base]);

  useEffect(() => {
    // Deferred by a microtask so the first render is never interrupted by the
    // state the load produces.
    void Promise.resolve().then(loadLinks);
    const pending = inFlight.current;
    return () => {
      listRequest.current?.controller.abort();
      redemptionRequest.current?.controller.abort();
      for (const controller of pending) controller.abort();
      pending.clear();
    };
  }, [loadLinks]);

  async function mutate(
    path: string,
    token: string,
    keyHolder: () => string,
    onSuccess: (payload: unknown) => void
  ) {
    setActionState("working");
    setInvalidField("");
    const data = new FormData();
    data.set("_csrf", token);
    data.set("idempotency_key", keyHolder());
    const controller = new AbortController();
    inFlight.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), MUTATION_TIMEOUT_MS);
    try {
      const response = await fetch(path, { method: "POST", body: data, signal: controller.signal });
      if (!response.ok) {
        setActionState(await failureState(response, setInvalidField));
        return false;
      }
      onSuccess(await response.json().catch(() => null));
      setActionState("idle");
      return true;
    } catch {
      // The key is kept on purpose: the next attempt replays rather than repeats.
      setActionState("unavailable");
      return false;
    } finally {
      clearTimeout(timeout);
      inFlight.current.delete(controller);
    }
  }

  async function submit() {
    if (actionState === "working") return;
    // O botão está acionável de propósito quando falta preencher algo. Aqui é
    // onde ele diz o que falta e leva o foco até lá, em vez de não fazer nada.
    if (createBlocked) {
      if (label.trim().length === 0) {
        setMissingLabel(true);
        labelInput.current?.focus();
      } else {
        seatsInput.current?.focus();
      }
      return;
    }
    setMissingLabel(false);
    createKey.current ??= crypto.randomUUID();
    setActionState("working");
    setInvalidField("");
    const data = new FormData();
    data.set("_csrf", csrf.create);
    data.set("idempotency_key", createKey.current);
    data.set("label", label.trim());
    data.set("max_uses", maxUses.trim());
    data.set("expires_at", expiresAt.trim());
    const controller = new AbortController();
    inFlight.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), MUTATION_TIMEOUT_MS);
    try {
      const response = await fetch(base, { method: "POST", body: data, signal: controller.signal });
      if (!response.ok) {
        const failure = await failureState(response, setInvalidField);
        // A rejected request will never be accepted as a replay, so the key goes.
        if (failure === "invalid" || failure === "forbidden") createKey.current = null;
        setActionState(failure);
        return;
      }
      const payload = await response.json() as { url?: string };
      createKey.current = null;
      setIssuedUrl(typeof payload.url === "string" ? payload.url : null);
      setCopied(false);
      setLabel("");
      setMaxUses("");
      setExpiresAt("");
      setActionState("idle");
      await loadLinks();
      queueMicrotask(() => listHeading.current?.focus());
    } catch {
      setActionState("unavailable");
    } finally {
      clearTimeout(timeout);
      inFlight.current.delete(controller);
    }
  }

  function keyFor(action: string, linkId: string) {
    const composed = `${action}:${linkId}`;
    const existing = mutationKeys.current.get(composed);
    if (existing) return existing;
    const minted = crypto.randomUUID();
    mutationKeys.current.set(composed, minted);
    return minted;
  }

  async function revoke(link: AccessLink) {
    setRevokeTarget(null);
    const done = await mutate(
      `${base}/${encodeURIComponent(link.access_link_id)}/revoke`,
      csrf.revoke,
      () => keyFor("revoke", link.access_link_id),
      () => mutationKeys.current.delete(`revoke:${link.access_link_id}`)
    );
    if (done) await loadLinks();
  }

  async function rotate(link: AccessLink) {
    const done = await mutate(
      `${base}/${encodeURIComponent(link.access_link_id)}/rotate`,
      csrf.rotate,
      () => keyFor("rotate", link.access_link_id),
      (payload) => {
        mutationKeys.current.delete(`rotate:${link.access_link_id}`);
        const url = (payload as { url?: string } | null)?.url;
        setIssuedUrl(typeof url === "string" ? url : null);
        setCopied(false);
      }
    );
    if (done) await loadLinks();
  }

  async function loadRedemptions(link: AccessLink) {
    redemptionRequest.current?.controller.abort();
    if (openRedemptions === link.access_link_id) {
      setOpenRedemptions(null);
      redemptionRequest.current = null;
      return;
    }
    setOpenRedemptions(link.access_link_id);
    setRedemptions([]);
    const controller = new AbortController();
    redemptionRequest.current = { controller, linkId: link.access_link_id };
    const timeout = setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS);
    try {
      const response = await fetch(
        `${base}/${encodeURIComponent(link.access_link_id)}/redemptions`,
        { cache: "no-store", signal: controller.signal }
      );
      // Another link may have been opened while this was in flight.
      if (redemptionRequest.current?.linkId !== link.access_link_id) return;
      if (!response.ok) return;
      const payload = await response.json() as { redemptions?: Redemption[] };
      if (redemptionRequest.current?.linkId !== link.access_link_id) return;
      setRedemptions(Array.isArray(payload.redemptions) ? payload.redemptions : []);
    } catch {
      if (redemptionRequest.current?.linkId === link.access_link_id) setRedemptions([]);
    } finally {
      clearTimeout(timeout);
      if (redemptionRequest.current?.linkId === link.access_link_id) {
        redemptionRequest.current = null;
      }
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

  // These inputs live inside the product editor's form, and pressing Enter there
  // would submit the draft instead of creating the link.
  function createOnEnter(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    void submit();
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

      <div className="access-link-form" onKeyDown={createOnEnter}>
        <fieldset aria-label="Novo link de acesso" disabled={actionState === "working"}>
          {/*
            Rótulo ligado por `htmlFor`/`id`, e ajuda e erro FORA do rótulo. Com
            os textos dentro dele, o nome acessível do campo viraria "Vagas De 1
            a 100.000 vagas…" — o leitor de tela leria a ajuda como se fosse o
            nome do campo. `aria-describedby` é o que os liga sem poluir o nome.
          */}
          <div className="access-link-field">
            <label htmlFor="access-link-label">Nome do link</label>
            <input
              id="access-link-label"
              ref={labelInput}
              value={label}
              onChange={(event) => { setLabel(event.target.value); setMissingLabel(false); }}
              maxLength={80}
              autoComplete="off"
              placeholder="Ex.: Turma Royalty Seguros"
              aria-required="true"
              aria-invalid={invalidField === "label" || missingLabel}
              aria-describedby={missingLabel ? "access-link-label-required" : undefined}
            />
            {missingLabel ? (
              <span id="access-link-label-required" className="form-error" role="alert">
                Dê um nome ao link para conseguir criá-lo.
              </span>
            ) : null}
          </div>
          <div className="access-link-field">
            <label htmlFor="access-link-seats">Vagas</label>
            <input
              id="access-link-seats"
              ref={seatsInput}
              type="number"
              value={maxUses}
              onChange={(event) => setMaxUses(keepDigits(event.target.value))}
              // O campo recusa a tecla impossível em vez de aceitar e reclamar
              // depois: `type="number"` deixaria passar "e", "+" e "-".
              onKeyDown={(event) => { if (isRejectedNumericKey(event.key)) event.preventDefault(); }}
              // Sem isto, rolar a página sobre o campo focado altera as vagas.
              onWheel={(event) => event.currentTarget.blur()}
              min={SEAT_RANGE.min}
              max={SEAT_RANGE.max}
              step={1}
              inputMode="numeric"
              autoComplete="off"
              placeholder="Ilimitado"
              aria-invalid={invalidField === "max_uses" || seatMessage !== null}
              aria-describedby={seatMessage ? "access-link-seats-error" : "access-link-seats-help"}
            />
            {/* O erro toma o lugar da ajuda em vez de se empilhar sob ela: as
                duas dizem o mesmo limite, e mostrar as duas era repetir a
                mesma frase duas vezes logo abaixo do campo. */}
            {seatMessage ? (
              <span id="access-link-seats-error" className="form-error" role="alert">{seatMessage}</span>
            ) : (
              <span id="access-link-seats-help" className="field-help">{SEAT_HELP}</span>
            )}
          </div>
          <div className="access-link-field">
            <label htmlFor="access-link-expires">Validade</label>
            <input
              id="access-link-expires"
              type="datetime-local"
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
              autoComplete="off"
              aria-invalid={invalidField === "expires_at"}
            />
          </div>
          {/*
            O botão não é desligado por campo vazio, nem com `disabled` nem com
            `aria-disabled` — ambos anunciam "indisponível" e tiram do vendedor
            a única forma de descobrir o que falta. Ele aciona sempre: quando
            algo falta, `submit` diz o que é e leva o foco até lá. Desligado, o
            botão fica reservado ao que é mesmo momentâneo (o envio em curso),
            e aí o próprio rótulo vira "Criando…".
          */}
          <div className="access-link-field">
            {/* Espaço inquebrável: um espaço comum colapsaria e o espaçador
                ficaria sem a altura de um rótulo. */}
            <span className="access-link-label-spacer" aria-hidden="true">{"\u00a0"}</span>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void submit()}
            >
              {actionState === "working" ? "Criando…" : "Criar link"}
            </button>
          </div>
        </fieldset>
        <div className="form-feedback" aria-live="polite" aria-atomic="true">
          {actionState === "invalid" ? (
            <p className="form-error">Revise o campo destacado e tente de novo.</p>
          ) : null}
          {actionState === "forbidden" ? (
            <p className="form-error">Sua sessão mudou. Entre novamente antes de tentar.</p>
          ) : null}
          {actionState === "unavailable" ? (
            <p className="form-note">
              A API não respondeu. Tentar de novo repete a mesma operação, sem duplicar nada.
            </p>
          ) : null}
          {actionState === "error" ? (
            <p className="form-error">Não foi possível concluir a ação.</p>
          ) : null}
        </div>
      </div>

      <h3 ref={listHeading} tabIndex={-1}>Links criados</h3>
      {listState === "loading" ? <p className="empty-state">Carregando links…</p> : null}
      {listState === "unauthorized" ? <p className="empty-state">Sua sessão expirou.</p> : null}
      {listState === "forbidden" ? (
        <p className="empty-state">Você não tem permissão nesta loja.</p>
      ) : null}
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
        const open = openRedemptions === link.access_link_id;
        const listId = `redemptions-${link.access_link_id}`;
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
              <button
                type="button"
                className="secondary-button"
                aria-expanded={open}
                aria-controls={listId}
                onClick={() => void loadRedemptions(link)}
              >
                {open ? "Ocultar quem entrou" : "Ver quem entrou"}
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
            {open ? (
              <ul className="access-link-redemptions" id={listId}>
                {redemptions.length === 0 ? <li>Ninguém entrou por este link ainda.</li> : null}
                {redemptions.map((entry) => (
                  <li key={entry.entitlement_id}>Entrou em {formatDate(entry.redeemed_at)}</li>
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
