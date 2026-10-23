// Ativação de vendas pelo Financial (#382): o que o navegador e o BFF dividem.
// A ação de CSRF fica aqui, e não em mutation-protection.server.ts, porque é
// só um nome; o mecanismo (csrfTokenForSession/verifyMutation) é o da Plataforma.
export const ACTIVATE_SELLER_FINANCIAL_CSRF_ACTION = "POST:/api/financial/seller-activation";

export type FinancialActivationStatus = "active" | "available" | "blocked";
export type FinancialActivationReason =
  | "unconfigured"
  | "channel_unavailable"
  | "channel_inactive"
  | "payout_account_not_ready";

export const FINANCIAL_ACTIVATION_REASON_COPY: Record<FinancialActivationReason, string> = {
  unconfigured: "A ativação ainda não está disponível. Fale com o suporte.",
  channel_unavailable: "A ativação está temporariamente indisponível. Tente mais tarde.",
  channel_inactive: "As vendas desta loja estão pausadas. Fale com o suporte.",
  payout_account_not_ready:
    "Conclua a conta de recebimentos acima antes de ativar as vendas."
};

const HEX_DIGITS = "0123456789abcdef";
const UUID_DASHES = new Set([8, 13, 18, 23]);
const UUID_LENGTH = 36;

// UUID canônico em minúsculas (8-4-4-4-12), sem regex. A API valida o id de
// novo; aqui só se recusa cedo um caminho que não pode ser de loja.
export function isCanonicalUuid(value: string): boolean {
  if (value.length !== UUID_LENGTH) return false;
  return [...value].every((character, index) =>
    UUID_DASHES.has(index) ? character === "-" : HEX_DIGITS.includes(character)
  );
}
