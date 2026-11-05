"use client";

import { FormEvent, useRef, useState } from "react";

import {
  billingPeriodLabel,
  formatOfferPrice,
  OFFER_BILLING_PERIODS,
  parsePriceInputToMinor
} from "@/lib/offer-price";
import { keepPriceCharacters } from "@/lib/numeric-field";

import {
  canBecomeDefault,
  canCreateOffer,
  MAX_OFFERS_PER_PRODUCT,
  OFFER_CURRENCIES,
  offerErrorMessage,
  orderedOffers,
  type OfferStatus,
  type ProductOfferSummary
} from "./product-offers";
import { ChoiceSelect } from "./ui/choice-select";

// Painel de ofertas extras do produto (#553, fase 2b). Só é montado quando a
// api diz `multi_offer_enabled`; com a lista de lojas vazia ele nunca aparece.
// Preço, moeda, limite e quem pode são decididos pela api (Decisão 005).

interface ProductOffersPanelProps {
  sellerId: string;
  productId: string;
  offers: readonly ProductOfferSummary[];
  csrfCreate: string;
  csrfDefault: string;
  csrfStatus: string;
  disabled: boolean;
  onChanged: () => Promise<boolean>;
}

type Busy = { kind: "idle" } | { kind: "saving"; target: string };

const STATUS_LABEL: Record<OfferStatus, string> = {
  draft: "Rascunho",
  active: "À venda",
  archived: "Encerrada"
};

export function ProductOffersPanel(props: ProductOffersPanelProps) {
  const { offers, disabled } = props;
  const [busy, setBusy] = useState<Busy>({ kind: "idle" });
  const [message, setMessage] = useState<string>("");
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState(OFFER_CURRENCIES[0]!.value);
  const [period, setPeriod] = useState<string>(OFFER_BILLING_PERIODS[0]!.value);
  // Uma chave por comando: um retry depois de resposta perdida vira replay.
  const pending = useRef<{ command: string; key: string; offerId: string } | null>(null);

  const ordered = orderedOffers(offers);
  const isBusy = busy.kind === "saving";
  const priceMinor = parsePriceInputToMinor(price);
  const formReady = title.trim().length >= 2 && title === title.trim() && priceMinor !== null;

  function commandKey(command: string) {
    if (pending.current?.command === command) return pending.current;
    const next = { command, key: crypto.randomUUID(), offerId: crypto.randomUUID() };
    pending.current = next;
    return next;
  }

  async function send(target: string, url: string, method: "POST" | "PATCH", data: FormData) {
    setBusy({ kind: "saving", target });
    setMessage("");
    try {
      const response = await fetch(url, { method, body: data });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { error?: unknown } | null;
        setMessage(offerErrorMessage(response.status, typeof body?.error === "string" ? body.error : undefined));
        return false;
      }
      const refreshed = await props.onChanged();
      if (refreshed) pending.current = null;
      return true;
    } catch {
      setMessage(offerErrorMessage(503, undefined));
      return false;
    } finally {
      setBusy({ kind: "idle" });
    }
  }

  async function createOffer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!formReady || priceMinor === null) return;
    const command = `create:${title}|${summary}|${priceMinor}|${currency}|${period}`;
    const { key, offerId } = commandKey(command);
    const data = new FormData();
    data.set("_csrf", props.csrfCreate);
    data.set("idempotency_key", key);
    data.set("offer_id", offerId);
    data.set("title", title);
    data.set("summary", summary);
    data.set("unit_amount_minor", String(priceMinor));
    data.set("currency", currency);
    data.set("billing_period", period);
    const created = await send("new", `/api/sellers/${props.sellerId}/products/${props.productId}/offers`, "POST", data);
    if (created) {
      setTitle("");
      setSummary("");
      setPrice("");
    }
  }

  async function makeDefault(offer: ProductOfferSummary) {
    const { key } = commandKey(`default:${offer.id}@${offer.version}`);
    const data = new FormData();
    data.set("_csrf", props.csrfDefault);
    data.set("idempotency_key", key);
    data.set("expected_offer_version", String(offer.version));
    await send(offer.id, `/api/sellers/${props.sellerId}/products/${props.productId}/offers/${offer.id}/default`, "POST", data);
  }

  async function changeStatus(offer: ProductOfferSummary, next: OfferStatus) {
    const { key } = commandKey(`status:${offer.id}:${offer.status}->${next}@${offer.version}`);
    const data = new FormData();
    data.set("_csrf", props.csrfStatus);
    data.set("idempotency_key", key);
    data.set("expected_status", offer.status);
    data.set("status", next);
    data.set("expected_offer_version", String(offer.version));
    await send(offer.id, `/api/sellers/${props.sellerId}/products/${props.productId}/offers/${offer.id}/status`, "PATCH", data);
  }

  return (
    <section className="offer-status-panel" aria-labelledby="product-offers-title">
      <p className="eyebrow">Ofertas do produto</p>
      <h2 id="product-offers-title">Outras ofertas</h2>
      <p>A vitrine e o checkout vendem a oferta padrão. As outras ficam prontas para trocar de padrão sem mudar o preço de quem já está comprando.</p>
      <ul className="product-offers-list" aria-label="Ofertas">
        {ordered.map((offer) => (
          <li key={offer.id}>
            <strong>{offer.title}</strong>
            {offer.isDefault ? <span className="badge"> Padrão</span> : null}
            <span> · {offer.unitAmountMinor ? formatOfferPrice(offer.unitAmountMinor, offer.currency) : "sem preço"} · {billingPeriodLabel(offer.billingPeriod)} · {STATUS_LABEL[offer.status]}</span>
            {offer.isDefault ? null : (
              <span className="product-offers-actions">
                {canBecomeDefault(offer, offers) ? (
                  <button type="button" className="secondary-button" disabled={disabled || isBusy} onClick={() => void makeDefault(offer)}>Tornar padrão</button>
                ) : null}
                {offer.status === "draft" ? (
                  <button type="button" className="secondary-button" disabled={disabled || isBusy} onClick={() => void changeStatus(offer, "active")}>Ativar vendas</button>
                ) : null}
                {offer.status === "active" ? (
                  <button type="button" className="secondary-button" disabled={disabled || isBusy} onClick={() => void changeStatus(offer, "draft")}>Voltar a rascunho</button>
                ) : null}
                {offer.status !== "archived" ? (
                  <button type="button" className="secondary-button" disabled={disabled || isBusy} onClick={() => void changeStatus(offer, "archived")}>Encerrar</button>
                ) : null}
              </span>
            )}
          </li>
        ))}
      </ul>
      {message ? <p className="form-error" role="alert">{message}</p> : null}
      {canCreateOffer(offers) ? (
        <form onSubmit={(event) => void createOffer(event)} aria-labelledby="product-offer-new-title">
          <fieldset disabled={disabled || isBusy}>
            <legend id="product-offer-new-title">Nova oferta</legend>
            <label className="editor-field">Nome no checkout
              <input id="new_offer_title" name="new_offer_title" value={title} onChange={(event) => setTitle(event.target.value)} minLength={2} maxLength={120} required autoComplete="off"/>
            </label>
            <label className="editor-field">Descrição no checkout
              <textarea id="new_offer_summary" name="new_offer_summary" value={summary} onChange={(event) => setSummary(event.target.value)} maxLength={500} rows={3} autoComplete="off"/>
            </label>
            <label className="editor-field">Preço
              <input id="new_offer_price" name="new_offer_price" value={price} onChange={(event) => setPrice(keepPriceCharacters(event.target.value))} maxLength={12} inputMode="decimal" autoComplete="off" aria-invalid={price !== "" && priceMinor === null}/>
            </label>
            <ChoiceSelect<string> label="Moeda" id="new_offer_currency" name="new_offer_currency" value={currency} options={OFFER_CURRENCIES} onChange={setCurrency} disabled={disabled || isBusy}/>
            <ChoiceSelect<string> label="Cobrança" id="new_offer_billing_period" name="new_offer_billing_period" value={period} options={OFFER_BILLING_PERIODS} onChange={setPeriod} disabled={disabled || isBusy}/>
            <button className="primary-button" type="submit" disabled={!formReady || isBusy}>{busy.kind === "saving" && busy.target === "new" ? "Criando…" : "Criar oferta em rascunho"}</button>
          </fieldset>
        </form>
      ) : <p>Este produto já tem {MAX_OFFERS_PER_PRODUCT} ofertas, o máximo.</p>}
    </section>
  );
}
