"use client";

import { loadStripe } from "@stripe/stripe-js";
import type { Stripe, StripeElements, StripePaymentElement } from "@stripe/stripe-js";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  parseRegistrationCheckoutIntent,
  parseRegistrationCheckoutQuote,
  type RegistrationCheckoutQuote
} from "@/lib/checkout-workspace-contract";

interface CheckoutWorkspaceProps {
  productTitle: string;
  priceLabel: string;
  financialProductKey: string;
}

type Stage = "email" | "preparing" | "payment" | "confirming" | "completing" | "success";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const COMPLETION_POLL_ATTEMPTS = 8;
const COMPLETION_POLL_DELAY_MS = 1_500;

export function CheckoutWorkspace({ productTitle, priceLabel, financialProductKey }: CheckoutWorkspaceProps) {
  const [stage, setStage] = useState<Stage>("email");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [quote, setQuote] = useState<RegistrationCheckoutQuote | null>(null);
  const stripeRef = useRef<Stripe | null>(null);
  const elementsRef = useRef<StripeElements | null>(null);
  const paymentElementRef = useRef<StripePaymentElement | null>(null);
  const mountNodeRef = useRef<HTMLDivElement | null>(null);
  const mounted = useRef(true);
  const [intentReady, setIntentReady] = useState<{ publishableKey: string; stripeAccountId: string; clientSecret: string } | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (stage !== "payment" || !intentReady || !mountNodeRef.current) return;
    let cancelled = false;
    void (async () => {
      const stripe = await loadStripe(intentReady.publishableKey, { stripeAccount: intentReady.stripeAccountId });
      if (cancelled || !stripe || !mountNodeRef.current) return;
      stripeRef.current = stripe;
      const elements = stripe.elements({ clientSecret: intentReady.clientSecret });
      elementsRef.current = elements;
      const paymentElement = elements.create("payment");
      paymentElementRef.current = paymentElement;
      paymentElement.mount(mountNodeRef.current);
    })();
    return () => {
      cancelled = true;
      paymentElementRef.current?.unmount();
      paymentElementRef.current = null;
      elementsRef.current = null;
      stripeRef.current = null;
    };
  }, [stage, intentReady]);

  const startCheckout = useCallback(async (buyerEmail: string) => {
    setStage("preparing");
    setMessage("");
    try {
      // Best-effort: registers the anonymous checkout context for this
      // e-mail/product pair. Its response isn't consumed here — quote is the
      // authoritative next step — so a transient failure here doesn't block
      // the purchase attempt.
      await fetch("/api/registration-checkout/financial/me/product-onboarding", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: buyerEmail, financial_product_key: financialProductKey })
      }).catch(() => null);

      const quoteResponse = await fetch("/api/registration-checkout/financial/me/registration-checkout/quote", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: buyerEmail, financial_product_key: financialProductKey })
      });
      const quoteValue: unknown = await quoteResponse.json().catch(() => null);
      const parsedQuote = quoteResponse.ok ? parseRegistrationCheckoutQuote(quoteValue) : null;
      if (!parsedQuote) {
        if (!mounted.current) return;
        setMessage("Não foi possível calcular o valor da compra agora. Tente novamente.");
        setStage("email");
        return;
      }
      if (!mounted.current) return;
      setQuote(parsedQuote);

      const intentResponse = await fetch("/api/registration-checkout/financial/me/registration-checkout/intent", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ order_id: parsedQuote.orderId })
      });
      const intentValue: unknown = await intentResponse.json().catch(() => null);
      const parsedIntent = intentResponse.ok ? parseRegistrationCheckoutIntent(intentValue) : null;
      if (!parsedIntent) {
        if (!mounted.current) return;
        setMessage("Não foi possível preparar o pagamento agora. Tente novamente.");
        setStage("email");
        return;
      }
      if (!mounted.current) return;
      setIntentReady({
        publishableKey: parsedIntent.publishableKey,
        stripeAccountId: parsedIntent.stripeAccountId,
        clientSecret: parsedIntent.clientSecret
      });
      setStage("payment");
    } catch {
      if (!mounted.current) return;
      setMessage("Checkout indisponível no momento. Tente novamente em instantes.");
      setStage("email");
    }
  }, [financialProductKey]);

  function submitEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = email.trim().toLowerCase();
    if (!EMAIL_PATTERN.test(trimmed)) {
      setMessage("Informe um e-mail válido para continuar.");
      return;
    }
    void startCheckout(trimmed);
  }

  async function submitPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const stripe = stripeRef.current;
    const elements = elementsRef.current;
    if (!stripe || !elements || !quote) return;
    setStage("confirming");
    setMessage("");
    const result = await stripe.confirmPayment({
      elements,
      redirect: "if_required",
      confirmParams: { return_url: window.location.href }
    });
    if (!mounted.current) return;
    if (result.error) {
      setMessage(result.error.message ?? "Não foi possível concluir o pagamento. Verifique os dados e tente novamente.");
      setStage("payment");
      return;
    }
    await completeCheckout(quote.orderId);
  }

  async function completeCheckout(orderId: string) {
    setStage("completing");
    for (let attempt = 0; attempt < COMPLETION_POLL_ATTEMPTS; attempt += 1) {
      const response = await fetch("/api/checkout/complete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ order_id: orderId })
      });
      if (response.ok) {
        if (!mounted.current) return;
        setStage("success");
        return;
      }
      if (response.status !== 402) {
        if (!mounted.current) return;
        setMessage("O pagamento foi recebido, mas a liberação do acesso falhou. Tente novamente em instantes.");
        setStage("payment");
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, COMPLETION_POLL_DELAY_MS));
    }
    if (!mounted.current) return;
    setMessage("O pagamento foi recebido e o acesso está sendo processado. Atualize a página em instantes.");
    setStage("payment");
  }

  if (stage === "success") {
    return <section className="checkout-card checkout-success" aria-live="polite">
      <p className="eyebrow">Compra aprovada</p>
      <h2>Tudo certo com {productTitle}</h2>
      <p>Seu pagamento foi confirmado. Entre com o e-mail usado na compra para acessar o produto.</p>
      <Link className="primary-button" href="/auth/login">Entrar para acessar</Link>
    </section>;
  }

  if (stage === "email" || stage === "preparing") {
    return <section className="checkout-card" aria-labelledby="checkout-product-title">
      <div className="checkout-summary">
        <div><p className="eyebrow">Checkout seguro</p><h2 id="checkout-product-title">{productTitle}</h2></div>
        <strong>{priceLabel}</strong>
      </div>
      <div className="checkout-trust">
        <span>Pagamento processado pela Stripe</span>
        <span>Dados protegidos</span>
        <span>Acesso liberado após confirmação</span>
      </div>
      <form onSubmit={submitEmail} noValidate>
        <label htmlFor="checkout-email">E-mail para acesso</label>
        <input
          id="checkout-email"
          name="email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="email"
          required
          disabled={stage === "preparing"}
        />
        <p className="field-help">Você vai usar este e-mail para entrar depois da compra.</p>
        {message ? <p className="form-error" role="alert" aria-live="assertive">{message}</p> : null}
        <button className="primary-button checkout-submit" type="submit" disabled={stage === "preparing"} aria-busy={stage === "preparing"}>
          {stage === "preparing" ? "Preparando pagamento…" : `Comprar por ${priceLabel}`}
        </button>
      </form>
    </section>;
  }

  return <section className="checkout-card" aria-labelledby="checkout-product-title">
    <div className="checkout-summary">
      <div><p className="eyebrow">Checkout seguro</p><h2 id="checkout-product-title">{productTitle}</h2></div>
      <strong>{priceLabel}</strong>
    </div>
    <form onSubmit={(event) => void submitPayment(event)} noValidate>
      <div ref={mountNodeRef} className="checkout-payment-element" />
      {message ? <p className="form-error" role="alert" aria-live="assertive">{message}</p> : null}
      <button
        className="primary-button checkout-submit"
        type="submit"
        disabled={stage === "confirming" || stage === "completing"}
        aria-busy={stage === "confirming" || stage === "completing"}
      >
        {stage === "confirming" ? "Confirmando pagamento…" : stage === "completing" ? "Liberando acesso…" : `Pagar ${priceLabel}`}
      </button>
    </form>
  </section>;
}
