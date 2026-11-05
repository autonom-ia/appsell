import "server-only";

import { CAMPOS_UTM } from "./attribution";
import { atribuicaoLiberada } from "./pilot";
import { atribuicaoDoPedido } from "./request-attribution";
import { chamarRastreio, registrarFalha } from "./servico.server";

interface PedidoComCookies {
  cookies: { get(nome: string): { value: string } | undefined };
}

const CAMINHO = "/v1/public/tracking/checkout-attribution";

// Chamada pelo checkout logo depois de criar o intent (#563). Nunca lança erro
// e nunca muda o checkout: sem piloto, sem aceite, sem cookie válido ou com a
// API fora do ar, o checkout segue igual e só a atribuição se perde.
export async function registrarAtribuicaoDoCheckout(
  request: PedidoComCookies,
  sellerId: string,
  checkoutIntentId: string,
  agora: number = Math.floor(Date.now() / 1000)
): Promise<void> {
  try {
    if (!atribuicaoLiberada(sellerId)) return;
    const atribuicao = atribuicaoDoPedido(request.cookies, agora);
    if (atribuicao === null) return;
    const utm = Object.fromEntries(CAMPOS_UTM.map((campo) => [campo, atribuicao.utm[campo] ?? null]));
    await chamarRastreio(CAMINHO, {
      seller_organization_id: sellerId,
      purchase_reference: checkoutIntentId,
      link_id: atribuicao.link,
      ...utm,
      visited_at: atribuicao.visitadoEm
    });
  } catch (error) {
    // chamarRastreio já registrou a falha da API; aqui só o erro inesperado.
    if (!(error instanceof Error && error.message === "tracking_unavailable")) {
      registrarFalha(CAMINHO, "unexpected", error, null);
    }
  }
}
