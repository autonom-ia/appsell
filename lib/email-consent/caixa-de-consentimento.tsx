"use client";

import { useId } from "react";

import { TEXTO_DA_CAIXA_DE_CONSENTIMENTO, type OrigemDoConsentimento } from "./texto";

interface CaixaDeConsentimentoProps {
  marcada: boolean;
  onChange: (marcada: boolean) => void;
  disabled?: boolean;
}

// A caixa vem desmarcada (quem usa começa com `marcada=false`), e comprar ou
// entrar não depende dela. No checkout, grave com `gravarConsentimentoDeEmail`
// só se a pessoa marcou: desmarcada ali não revoga nada. Na conta, ao salvar,
// grave se ela marcou e revogue com `revogarConsentimentoDeEmail` se
// desmarcou o que estava marcado; sem mudança, não chame nada.
export function CaixaDeConsentimentoDeEmail({ marcada, onChange, disabled }: CaixaDeConsentimentoProps) {
  const id = useId();
  return (
    <p className="checkbox-row">
      <input
        id={id}
        type="checkbox"
        checked={marcada}
        disabled={disabled}
        onChange={(evento) => onChange(evento.target.checked)}
      />
      <label htmlFor={id}>{TEXTO_DA_CAIXA_DE_CONSENTIMENTO}</label>
    </p>
  );
}

// Ponto único da gravação, para o checkout e para a conta. Devolve se gravou.
// Nunca lança: uma falha aqui não pode travar a compra.
export async function gravarConsentimentoDeEmail(
  pedido: { sellerId: string; csrf: string; origem: OrigemDoConsentimento },
  enviar: typeof fetch = fetch
): Promise<boolean> {
  return postar({ seller: pedido.sellerId, decision: "grant", source: pedido.origem, _csrf: pedido.csrf }, enviar);
}

// Revogação pela própria pessoa, em "Minha conta". Devolve se revogou. Nunca
// lança.
export async function revogarConsentimentoDeEmail(
  pedido: { sellerId: string; csrf: string },
  enviar: typeof fetch = fetch
): Promise<boolean> {
  return postar({ seller: pedido.sellerId, decision: "revoke", _csrf: pedido.csrf }, enviar);
}

async function postar(campos: Record<string, string>, enviar: typeof fetch): Promise<boolean> {
  try {
    const corpo = new URLSearchParams(campos);
    const resposta = await enviar("/api/email/consent", {
      method: "POST",
      body: corpo,
      keepalive: true,
      credentials: "same-origin"
    });
    return resposta.status === 204;
  } catch {
    return false;
  }
}
