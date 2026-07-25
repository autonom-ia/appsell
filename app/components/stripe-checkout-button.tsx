"use client";

import { FormEvent, useState } from "react";

interface StripeCheckoutButtonProps {
  sellerSlug: string;
  productSlug: string;
}

type State = "idle" | "submitting" | "error";

// Minimal REAL checkout entry point (spike #143) that lives ALONGSIDE the
// zero-charge DemoCheckoutWorkspace. Collects the buyer e-mail, POSTs to the
// create-session route (which reads the price server-side) and redirects the
// browser to the Stripe-hosted checkout page. No amount is sent from here.
export function StripeCheckoutButton({ sellerSlug, productSlug }: StripeCheckoutButtonProps) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>("idle");
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state === "submitting") return;
    setState("submitting");
    setMessage("");
    try {
      const response = await fetch("/api/checkout/stripe/create-session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          seller_slug: sellerSlug,
          product_slug: productSlug,
          buyer_email: email.trim().toLowerCase()
        })
      });
      const value: unknown = await response.json().catch(() => null);
      const url = isRecord(value) && typeof value.url === "string" ? value.url : null;
      if (!response.ok || !url) {
        setMessage("Não foi possível iniciar o pagamento. Nenhuma cobrança foi realizada.");
        setState("error");
        return;
      }
      // Hand the browser off to Stripe's hosted checkout.
      window.location.assign(url);
    } catch {
      setMessage("Falha de rede ao iniciar o pagamento. Tente novamente.");
      setState("error");
    }
  }

  return (
    <section className="checkout-card" aria-labelledby="stripe-checkout-title">
      <h2 id="stripe-checkout-title">Pagar com cartão</h2>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <label htmlFor="stripe-buyer-email">Seu e-mail</label>
        <input
          id="stripe-buyer-email"
          name="buyer_email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="email"
          maxLength={254}
          required
          disabled={state === "submitting"}
        />
        {message ? <p className="form-error" role="alert">{message}</p> : null}
        <button className="primary-button checkout-submit" type="submit" disabled={state === "submitting"}>
          {state === "submitting" ? "Redirecionando ao Stripe…" : "Ir para o pagamento"}
        </button>
      </form>
    </section>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
