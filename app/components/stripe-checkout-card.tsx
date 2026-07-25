"use client";

import { useRef, useState } from "react";

interface StripeCheckoutCardProps {
  sellerSlug: string;
  productSlug: string;
  sellerName: string;
  productTitle: string;
  priceLabel: string;
  /** E-mail VERIFICADO da sessão. É a conta cobrada e a que recebe o acesso. */
  accountEmail: string;
}

type State = "idle" | "submitting" | "error";

// Entrada do checkout REAL (Stripe), ao lado do DemoCheckoutWorkspace de custo
// zero. Três decisões de UX que vêm das regras da rota, não de gosto:
//
// 1. O e-mail NÃO é campo editável. A rota só cobra quando ele bate com o e-mail
//    verificado da sessão (senão qualquer um amarraria uma compra paga à própria
//    conta). Campo livre transformaria essa regra em armadilha: o comprador
//    digitaria outro endereço e levaria erro depois de já ter decidido pagar.
//    Mostramos a conta que será cobrada e como trocá-la.
// 2. O preço aparece ANTES do clique. Ninguém deve ser mandado a uma tela de
//    pagamento sem saber o valor.
// 3. Cada falha tem mensagem própria e diz se houve cobrança. Um "não foi
//    possível" genérico num passo de pagamento deixa a pessoa sem saber se pagou.
export function StripeCheckoutCard({
  sellerSlug,
  productSlug,
  sellerName,
  productTitle,
  priceLabel,
  accountEmail
}: StripeCheckoutCardProps) {
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<CheckoutError | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);

  function fail(next: CheckoutError) {
    setError(next);
    setState("error");
    // Foco no erro: sem isso quem usa leitor de tela ou teclado fica parado no
    // botão sem perceber por que nada aconteceu.
    requestAnimationFrame(() => errorRef.current?.focus());
  }

  async function submit() {
    if (state === "submitting") return;
    setState("submitting");
    setError(null);
    let response: Response;
    try {
      response = await fetch("/api/checkout/stripe/create-session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          seller_slug: sellerSlug,
          product_slug: productSlug,
          buyer_email: accountEmail
        })
      });
    } catch {
      fail(ERRORS.network);
      return;
    }
    const value: unknown = await response.json().catch(() => null);
    const url = isRecord(value) && typeof value.url === "string" ? value.url : null;
    if (response.ok && url) {
      // Entrega o browser ao checkout hospedado do Stripe. Mantém "submitting":
      // a navegação está a caminho e reabilitar o botão convidaria a um segundo
      // clique — que hoje criaria uma segunda cobrança (Refs #152).
      window.location.assign(url);
      return;
    }
    const code = isRecord(value) && typeof value.error === "string" ? value.error : "";
    fail(errorFor(response.status, code));
  }

  const busy = state === "submitting";

  return (
    <section className="checkout-card" aria-labelledby="stripe-checkout-title">
      <div>
        <p className="eyebrow">{sellerName}</p>
        <div className="checkout-summary">
          <h2 id="stripe-checkout-title">{productTitle}</h2>
          <strong>{priceLabel}</strong>
        </div>
      </div>

      <div className="checkout-account">
        <p className="checkout-account-label">Conta que receberá o acesso</p>
        <p className="checkout-account-email">{accountEmail}</p>
        <p className="field-help" id="stripe-account-help">
          O acesso ao curso fica vinculado a esta conta. Para usar outro e-mail,{" "}
          <a href="/auth/login">entre com ela</a> antes de pagar.
        </p>
      </div>

      <div className="checkout-trust">
        <span>Pagamento processado pelo Stripe</span>
        <span>Não guardamos os dados do seu cartão</span>
        <span>Acesso liberado após a confirmação</span>
      </div>

      {/* O valor aparece uma vez como número (no resumo) e uma vez como ação (no
          botão). Uma linha "Total" repetindo o mesmo número, sem desconto algum
          entre os dois, lia como se fossem dois valores diferentes — pior ainda
          porque os estilos herdados os pintavam de cores distintas. */}
      <div className="checkout-cta">
        {/* aria-live assertive: é falha em passo de pagamento, interrompe mesmo.
            tabIndex -1 recebe foco programático sem entrar na ordem de tabulação.
            O container existe sempre, para o erro não empurrar o botão para
            debaixo do cursor no instante do clique. */}
        <div
          ref={errorRef}
          tabIndex={-1}
          role="alert"
          aria-live="assertive"
          className={error ? "checkout-error" : "checkout-error is-empty"}
        >
          {error ? (
            <>
              <p className="form-error">{error.message}</p>
              <p className="field-help">{error.charged}</p>
              {error.action ? (
                <p className="field-help">
                  <a href={error.action.href}>{error.action.label}</a>
                </p>
              ) : null}
            </>
          ) : null}
        </div>

        <button
          className="primary-button checkout-submit"
          type="button"
          onClick={() => void submit()}
          disabled={busy}
          aria-busy={busy}
          aria-describedby="stripe-account-help"
        >
          {busy ? "Abrindo pagamento seguro…" : `Pagar ${priceLabel} com cartão`}
        </button>
        <p className="field-help checkout-cta-note">
          Cobrança única. Você revisa os dados no Stripe antes de confirmar.
        </p>
      </div>
    </section>
  );
}

interface CheckoutError {
  message: string;
  /** Sempre dizemos se houve cobrança: é a dúvida real de quem vê erro aqui. */
  charged: string;
  action?: { href: string; label: string };
}

const NO_CHARGE = "Nenhuma cobrança foi realizada.";

const ERRORS = {
  network: {
    message: "Não conseguimos falar com o servidor.",
    charged: `${NO_CHARGE} Verifique sua conexão e tente de novo.`
  },
  session: {
    message: "Sua sessão expirou.",
    charged: NO_CHARGE,
    action: { href: "/auth/login", label: "Entrar novamente" }
  },
  mismatch: {
    message: "Você está em outra conta.",
    charged: `${NO_CHARGE} Entre com a conta que deve receber o acesso.`,
    action: { href: "/auth/login", label: "Trocar de conta" }
  },
  unavailable: {
    message: "Produto indisponível para compra agora.",
    charged: NO_CHARGE
  },
  instability: {
    message: "Instabilidade momentânea no nosso lado.",
    charged: `${NO_CHARGE} Tente novamente em alguns instantes.`
  },
  generic: {
    message: "Não foi possível iniciar o pagamento.",
    charged: `${NO_CHARGE} Se persistir, fale com o suporte.`
  }
} satisfies Record<string, CheckoutError>;

// Mapeia status/código da rota para algo acionável. Os códigos são os que
// app/api/checkout/stripe/create-session devolve.
function errorFor(status: number, code: string): CheckoutError {
  if (status === 401) return ERRORS.session;
  if (code === "buyer_email_mismatch") return ERRORS.mismatch;
  if (code === "product_not_configured" || code === "product_not_purchasable") return ERRORS.unavailable;
  if (status === 404) return ERRORS.unavailable;
  if (status === 503 || status === 502) return ERRORS.instability;
  return ERRORS.generic;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
