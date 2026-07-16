"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

interface Quote {
  product: { title: string };
  seller: { name: string };
  offer: { unit_amount_minor: number; currency: "BRL" };
  already_owned: boolean;
  entitlement_id: string | null;
}

interface Payload { checkout: Quote; coupon_hint: string; csrf: { complete: string } }
type State = "loading" | "ready" | "submitting" | "success" | "error";

export function DemoCheckoutWorkspace() {
  const [state, setState] = useState<State>("loading");
  const [payload, setPayload] = useState<Payload | null>(null);
  const [coupon, setCoupon] = useState("AUTONOMIA100");
  const [message, setMessage] = useState("");
  const mounted = useRef(true);

  const load = useCallback(async (preserveSubmitting = false) => {
    const response = await fetch("/api/demo-checkout", { cache: "no-store" });
    const value: unknown = await response.json().catch(() => null);
    if (!response.ok || !isPayload(value)) throw new Error("checkout_unavailable");
    if (!mounted.current) return value;
    setPayload(value);
    setCoupon(value.coupon_hint);
    if (value.checkout.already_owned) setState("success");
    else if (!preserveSubmitting) setState("ready");
    return value;
  }, []);

  useEffect(() => {
    mounted.current = true;
    const start = window.setTimeout(() => void load().catch(() => setState("error")), 0);
    return () => { window.clearTimeout(start); mounted.current = false; };
  }, [load]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!payload || state === "submitting") return;
    setState("submitting");
    setMessage("");
    const form = new FormData();
    form.set("_csrf", payload.csrf.complete);
    form.set("idempotency_key", crypto.randomUUID());
    form.set("coupon_code", coupon.trim().toUpperCase());
    const response = await fetch("/api/demo-checkout", { method: "POST", body: form });
    await response.json().catch(() => null);
    if (!response.ok) {
      setMessage(response.status === 403 ? "A validação de segurança expirou. Atualize a página." :
        response.status === 422 ? "Cupom inválido. Confira o código e tente novamente." :
        "Não foi possível concluir agora. Nenhuma cobrança foi realizada.");
      setState("ready");
      return;
    }
    for (let attempt = 0; attempt < 32; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 250));
      const refreshed = await load(true).catch(() => null);
      if (refreshed?.checkout.already_owned) return;
    }
    setMessage("Pedido aprovado. O acesso ainda está sendo processado; tente atualizar.");
    setState("ready");
  }

  if (state === "loading") return <section className="checkout-card checkout-loading" aria-live="polite" aria-busy="true"><p>Validando oferta e identidade…</p></section>;
  if (state === "error" || !payload) return <section className="checkout-card" role="alert"><h2>Checkout indisponível</h2><p>Nenhuma cobrança foi realizada.</p><button className="primary-button" type="button" onClick={() => { setState("loading"); void load().catch(() => setState("error")); }}>Tentar novamente</button></section>;
  if (state === "success") return <section className="checkout-card checkout-success" aria-live="polite"><p className="eyebrow">Acesso liberado</p><h2>Compra demo aprovada</h2><p>O pedido de valor zero criou uma única compra e seu acesso ao curso. Nenhum provedor financeiro foi acionado.</p><Link className="primary-button" href="/library">Abrir minha biblioteca</Link></section>;

  const price = new Intl.NumberFormat("pt-BR", { style: "currency", currency: payload.checkout.offer.currency }).format(payload.checkout.offer.unit_amount_minor / 100);
  return <section className="checkout-card" aria-labelledby="checkout-product-title">
    <div className="checkout-summary"><div><p className="eyebrow">{payload.checkout.seller.name}</p><h2 id="checkout-product-title">{payload.checkout.product.title}</h2></div><strong>{price}</strong></div>
    <div className="checkout-trust"><span>Conta validada</span><span>Total calculado no servidor</span><span>Sem captura financeira</span></div>
    <form onSubmit={(event) => void submit(event)} noValidate>
      <label htmlFor="demo-coupon">Cupom de demonstração</label>
      <div className="checkout-coupon"><input id="demo-coupon" name="coupon_code" value={coupon} onChange={(event) => setCoupon(event.target.value.toUpperCase())} autoComplete="off" maxLength={32} disabled={state === "submitting"}/><span>100% OFF</span></div>
      <p className="field-help">Use <strong>{payload.coupon_hint}</strong> para zerar o pedido.</p>
      {message ? <p className="form-error" role="alert">{message}</p> : null}
      <div className="checkout-total"><span>Total após cupom</span><strong>R$ 0,00</strong></div>
      <button className="primary-button checkout-submit" type="submit" disabled={state === "submitting"}>{state === "submitting" ? "Processando acesso…" : "Concluir compra por R$ 0,00"}</button>
    </form>
  </section>;
}

function isPayload(value: unknown): value is Payload {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const checkout = record.checkout as Record<string, unknown> | undefined;
  const csrf = record.csrf as Record<string, unknown> | undefined;
  const offer = checkout?.offer as Record<string, unknown> | undefined;
  return typeof record.coupon_hint === "string" && typeof csrf?.complete === "string" &&
    typeof checkout?.already_owned === "boolean" &&
    (checkout.entitlement_id === null || typeof checkout.entitlement_id === "string") &&
    typeof (checkout.product as Record<string, unknown> | undefined)?.title === "string" &&
    typeof (checkout.seller as Record<string, unknown> | undefined)?.name === "string" &&
    typeof offer?.unit_amount_minor === "number" && offer.currency === "BRL";
}
