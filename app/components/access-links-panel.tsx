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
import {
  EXPIRY_PRESET_DAYS,
  SEAT_PRESETS,
  earliestExpiryDate,
  expiresAtFrom,
  maxUsesFrom,
  type ExpiryChoice,
  type SeatChoice
} from "@/lib/access-class";

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
  // Sem limite e sem prazo são o começo porque são a turma que a maioria cria:
  // um link para mandar hoje. Quem precisa de teto ou de data escolhe.
  const [seatChoice, setSeatChoice] = useState<SeatChoice>({ kind: "unlimited" });
  const [expiryChoice, setExpiryChoice] = useState<ExpiryChoice>({ kind: "never" });
  // Só vira verdadeiro quando a pessoa aciona o botão sem nome preenchido. O
  // formulário não abre reclamando de um campo que ninguém tentou usar ainda.
  const [missingLabel, setMissingLabel] = useState(false);
  const [missingExpiryDate, setMissingExpiryDate] = useState(false);
  // Lido quando a pessoa escolhe "Escolher data", não quando a lista carregou:
  // um formulário aberto desde ontem ofereceria um piso de ontem.
  const [calendarFloor, setCalendarFloor] = useState(0);

  // Derivado a cada render: o limite de vagas é conferido enquanto se digita,
  // não no envio.
  const seatMessage = seatChoice.kind === "custom" ? seatRangeMessage(readSeatInput(maxUses)) : null;
  const seatsMissing = seatChoice.kind === "custom" && maxUses.trim() === "";
  const dateMissing = expiryChoice.kind === "date" && expiryChoice.value.trim() === "";
  const createBlocked = label.trim().length === 0 || seatMessage !== null || seatsMissing || dateMissing;
  // O motivo fica na tela antes de acionar, não só depois. Um só de cada vez:
  // uma lista de pendências ao lado de um botão vivo assusta mais do que ajuda.
  const blockingReason = label.trim().length === 0
    ? "Falta o nome da turma."
    : seatsMissing || seatMessage !== null
      ? "Informe quantas vagas."
      : dateMissing
        ? "Escolha o último dia."
        : null;

  const listHeading = useRef<HTMLHeadingElement>(null);
  const labelInput = useRef<HTMLInputElement>(null);
  const seatsInput = useRef<HTMLInputElement>(null);
  const expiryInput = useRef<HTMLInputElement>(null);
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
      // A ordem aqui é a mesma de `blockingReason`. Fora de ordem, a tela
      // dizia "informe quantas vagas" e o foco caía no campo de data — a
      // pessoa lê uma coisa e o cursor vai para outra.
      if (label.trim().length === 0) {
        setMissingLabel(true);
        labelInput.current?.focus();
      } else if (seatsMissing || seatMessage !== null) {
        seatsInput.current?.focus();
      } else {
        setMissingExpiryDate(true);
        expiryInput.current?.focus();
      }
      return;
    }
    setMissingLabel(false);
    setMissingExpiryDate(false);
    createKey.current ??= crypto.randomUUID();
    setActionState("working");
    setInvalidField("");
    const data = new FormData();
    data.set("_csrf", csrf.create);
    data.set("idempotency_key", createKey.current);
    data.set("label", label.trim());
    data.set("max_uses", maxUsesFrom(seatChoice, maxUses));
    // A hora é lida aqui, no envio, e nunca durante a renderização: "sete dias"
    // conta a partir do momento em que a turma é criada.
    data.set("expires_at", expiresAtFrom(expiryChoice, Date.now()));
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
      setSeatChoice({ kind: "unlimited" });
      setExpiryChoice({ kind: "never" });
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
      <h2 id="access-links-title">Turmas</h2>
      <p>
        Cada turma tem um endereço próprio. Quem abrir entra com o próprio cadastro, e voltar
        depois não gasta outra vaga.
      </p>
      {!published ? (
        <p className="form-note" role="status">
          Coloque o produto no ar para as turmas começarem a funcionar. Você já pode criá-las.
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
        <fieldset aria-label="Nova turma" disabled={actionState === "working"}>
          {/*
            Rótulo ligado por `htmlFor`/`id`, e ajuda e erro FORA do rótulo. Com
            os textos dentro dele, o nome acessível do campo carregaria a ajuda
            junto — o leitor de tela leria "Vagas De 1 a 100.000 vagas…" como se
            fosse o nome. `aria-describedby` liga sem poluir o nome.
          */}
          <div className="access-link-field">
            <label htmlFor="access-link-label">Nome da turma</label>
            <input
              id="access-link-label"
              ref={labelInput}
              value={label}
              onChange={(event) => { setLabel(event.target.value); setMissingLabel(false); }}
              maxLength={80}
              autoComplete="off"
              placeholder="Ex.: Turma de agosto"
              aria-required="true"
              aria-invalid={invalidField === "label" || missingLabel}
              aria-describedby={missingLabel ? "access-link-label-required" : "access-link-label-help"}
            />
            {missingLabel ? (
              <span id="access-link-label-required" className="form-error" role="alert">
                Dê um nome à turma para conseguir criá-la.
              </span>
            ) : (
              <span id="access-link-label-help" className="field-help">
                Só você vê. Serve para reconhecer a turma na lista.
              </span>
            )}
          </div>

          {/*
            Vagas e prazo viraram escolha, não digitação. Os presets cobrem o
            que se decide de fato; a entrada livre continua existindo para o
            resto, e não aceita nada fora da faixa. Rádio de verdade, não botão
            com estado fingido: é o teclado e o leitor de tela que ganham.
          */}
          <fieldset className="access-link-choice">
            <legend>Quantas vagas</legend>
            <div className="chip-row">
              {SEAT_PRESETS.map((preset) => (
                <label key={preset} className={seatChoice.kind === "preset" && seatChoice.seats === preset ? "chip selected" : "chip"}>
                  <input
                    type="radio"
                    name="access_link_seats"
                    checked={seatChoice.kind === "preset" && seatChoice.seats === preset}
                    onChange={() => { setSeatChoice({ kind: "preset", seats: preset }); setMaxUses(""); }}
                  />
                  <span>{preset}</span>
                </label>
              ))}
              <label className={seatChoice.kind === "unlimited" ? "chip selected" : "chip"}>
                <input
                  type="radio"
                  name="access_link_seats"
                  checked={seatChoice.kind === "unlimited"}
                  onChange={() => { setSeatChoice({ kind: "unlimited" }); setMaxUses(""); }}
                />
                <span>Sem limite</span>
              </label>
              <label className={seatChoice.kind === "custom" ? "chip selected" : "chip"}>
                <input
                  type="radio"
                  name="access_link_seats"
                  checked={seatChoice.kind === "custom"}
                  onChange={() => { setSeatChoice({ kind: "custom" }); queueMicrotask(() => seatsInput.current?.focus()); }}
                />
                <span>Outro número</span>
              </label>
            </div>
            {seatChoice.kind === "custom" ? (
              <div className="access-link-field">
                <label htmlFor="access-link-seats">Número exato</label>
                <input
                  id="access-link-seats"
                  ref={seatsInput}
                  type="number"
                  value={maxUses}
                  onChange={(event) => setMaxUses(keepDigits(event.target.value))}
                  // O campo recusa a tecla impossível em vez de aceitar e
                  // reclamar depois: `type="number"` deixaria passar "e", "+" e "-".
                  onKeyDown={(event) => { if (isRejectedNumericKey(event.key)) event.preventDefault(); }}
                  // Sem isto, rolar a página sobre o campo focado altera as vagas.
                  onWheel={(event) => event.currentTarget.blur()}
                  min={SEAT_RANGE.min}
                  max={SEAT_RANGE.max}
                  step={1}
                  inputMode="numeric"
                  autoComplete="off"
                  aria-invalid={invalidField === "max_uses" || seatMessage !== null}
                  aria-describedby={seatMessage ? "access-link-seats-error" : "access-link-seats-help"}
                />
                {/* O erro toma o lugar da ajuda em vez de se empilhar sob ela:
                    as duas dizem o mesmo limite. */}
                {seatMessage ? (
                  <span id="access-link-seats-error" className="form-error" role="alert">{seatMessage}</span>
                ) : (
                  <span id="access-link-seats-help" className="field-help">{SEAT_HELP}</span>
                )}
              </div>
            ) : null}
          </fieldset>

          <fieldset className="access-link-choice">
            <legend>Até quando</legend>
            <div className="chip-row">
              {EXPIRY_PRESET_DAYS.map((days) => (
                <label key={days} className={expiryChoice.kind === "days" && expiryChoice.days === days ? "chip selected" : "chip"}>
                  <input
                    type="radio"
                    name="access_link_expiry"
                    checked={expiryChoice.kind === "days" && expiryChoice.days === days}
                    onChange={() => setExpiryChoice({ kind: "days", days })}
                  />
                  <span>{days} dias</span>
                </label>
              ))}
              <label className={expiryChoice.kind === "never" ? "chip selected" : "chip"}>
                <input
                  type="radio"
                  name="access_link_expiry"
                  checked={expiryChoice.kind === "never"}
                  onChange={() => setExpiryChoice({ kind: "never" })}
                />
                <span>Sem prazo</span>
              </label>
              <label className={expiryChoice.kind === "date" ? "chip selected" : "chip"}>
                <input
                  type="radio"
                  name="access_link_expiry"
                  checked={expiryChoice.kind === "date"}
                  onChange={() => {
                    setExpiryChoice({ kind: "date", value: "" });
                    setCalendarFloor(Date.now());
                    queueMicrotask(() => expiryInput.current?.focus());
                  }}
                />
                <span>Escolher data</span>
              </label>
            </div>
            {expiryChoice.kind === "date" ? (
              <div className="access-link-field">
                <label htmlFor="access-link-expires">Último dia</label>
                {/*
                  `type="date"`, não `datetime-local`: pedir hora e minuto para
                  fechar uma turma é precisão que ninguém tem. O dia escolhido
                  vale inteiro — fechar à meia-noite trancaria do lado de fora
                  justamente quem entrasse no dia que a pessoa quis incluir.

                  O piso do calendário vem do instante em que a lista chegou, e
                  nunca de um Date.now() no render: ler o relógio durante a
                  renderização faz a mesma árvore produzir telas diferentes.
                */}
                <input
                  id="access-link-expires"
                  ref={expiryInput}
                  type="date"
                  value={expiryChoice.value}
                  min={calendarFloor ? earliestExpiryDate(calendarFloor) : undefined}
                  onChange={(event) => setExpiryChoice({ kind: "date", value: event.target.value })}
                  autoComplete="off"
                  aria-invalid={invalidField === "expires_at" || missingExpiryDate}
                  aria-describedby={missingExpiryDate ? "access-link-expires-error" : "access-link-expires-help"}
                />
                {missingExpiryDate ? (
                  <span id="access-link-expires-error" className="form-error" role="alert">
                    Escolha o último dia da turma, ou use “Sem prazo”.
                  </span>
                ) : (
                  <span id="access-link-expires-help" className="field-help">A turma vale até o fim desse dia.</span>
                )}
              </div>
            ) : null}
          </fieldset>

          {/*
            O botão não é desligado por campo vazio, nem com `disabled` nem com
            `aria-disabled` — ambos anunciam "indisponível" e tiram do vendedor
            a única forma de descobrir o que falta (#254). O motivo fica visível
            ao lado dele antes mesmo de acionar, e acionar leva o foco até o
            campo. Desligado fica reservado ao que é momentâneo, o envio em
            curso, e aí o próprio rótulo vira "Criando…".
          */}
          <div className="access-link-submit">
            <button type="button" className="primary-button" onClick={() => void submit()}>
              {actionState === "working" ? "Criando…" : "Criar turma"}
            </button>
            {blockingReason ? <p className="field-help" id="access-link-blocked">{blockingReason}</p> : null}
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

      <h3 ref={listHeading} tabIndex={-1}>Turmas criadas</h3>
      {listState === "loading" ? <p className="empty-state">Carregando turmas…</p> : null}
      {listState === "unauthorized" ? <p className="empty-state">Sua sessão expirou.</p> : null}
      {listState === "forbidden" ? (
        <p className="empty-state">Você não tem permissão nesta loja.</p>
      ) : null}
      {listState === "unavailable" ? (
        <p className="empty-state">A API está indisponível. Tente novamente.</p>
      ) : null}
      {listState === "error" ? (
        <p className="empty-state">Não foi possível carregar as turmas.</p>
      ) : null}
      {listState === "ready" && links.length === 0 ? (
        <p className="empty-state">Nenhuma turma ainda. Crie a primeira acima.</p>
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
                {redemptions.length === 0 ? <li>Ninguém entrou nesta turma ainda.</li> : null}
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
              Manter a turma
            </button>
            <button type="button" className="danger-button" onClick={() => void revoke(revokeTarget)}>
              Revogar turma
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
