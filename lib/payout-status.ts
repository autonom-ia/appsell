// Deriva o estágio da conta de recebimentos a partir do cache de status que a
// API expõe. Puro e sem I/O de propósito: a tela decide o que mostrar a partir
// DESTE resultado, e o teste prova cada transição sem montar DOM.
//
// A ordem dos estágios codifica a sequência REAL do processo: o produtor cria
// a conta, envia a verificação de identidade, o provedor analisa, e só então a
// conta cobra e repassa. Nunca citamos o gateway na copy — "conta Autonom.ia"
// e "verificação de identidade" são o vocabulário do produto.

export interface PayoutAccountStatus {
  charges_enabled: boolean;
  payouts_enabled: boolean;
  details_submitted: boolean;
  disabled_reason: string | null;
}

export type PayoutStage =
  | "not_connected"
  | "verification_pending"
  | "under_review"
  | "action_required"
  | "active";

export function payoutStageFrom(account: PayoutAccountStatus | null): PayoutStage {
  if (!account) return "not_connected";
  if (account.charges_enabled && account.payouts_enabled) return "active";
  if (account.disabled_reason) return "action_required";
  if (!account.details_submitted) return "verification_pending";
  return "under_review";
}

export const PAYOUT_STAGE_COPY: Record<
  PayoutStage,
  { badge: string; title: string; description: string; action: string | null }
> = {
  not_connected: {
    badge: "Não conectada",
    title: "Receba suas vendas direto na sua conta",
    description:
      "Conecte sua conta Autonom.ia de recebimentos para que o valor de cada venda caia direto com você. Tenha em mãos um documento com foto (CPF) ou os dados da sua empresa (CNPJ) e os dados bancários de quem recebe.",
    action: "Conectar conta Autonom.ia"
  },
  verification_pending: {
    badge: "Verificação incompleta",
    title: "Falta concluir sua verificação",
    description:
      "Sua conta de recebimentos foi criada, mas a verificação de identidade parou no meio. Continue de onde você parou — leva poucos minutos.",
    action: "Continuar verificação"
  },
  under_review: {
    badge: "Em análise",
    title: "Verificação em análise",
    description:
      "Recebemos seus dados e a análise está em andamento. Normalmente conclui em minutos; casos que exigem revisão manual podem levar mais tempo. Você não precisa fazer nada agora.",
    action: null
  },
  action_required: {
    badge: "Ação necessária",
    title: "Sua conta precisa de atenção",
    description:
      "A verificação apontou uma pendência nos seus dados. Revise as informações para voltar a receber normalmente.",
    action: "Revisar dados"
  },
  active: {
    badge: "Pronta para receber",
    title: "Sua conta está ativa",
    description:
      "Tudo certo: cada venda cai direto na sua conta, e a comissão da plataforma é descontada automaticamente. Nenhuma ação necessária.",
    action: null
  }
};
