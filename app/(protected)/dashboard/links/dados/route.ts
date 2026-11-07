import { NextRequest } from "next/server";

import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { listDomainCampaignLinks } from "@/lib/internal-api-client.server";
import { csrfTokenForSession } from "@/lib/mutation-protection.server";
import { CAMPAIGN_LINK_CSRF_ACTION, pedidoDoPainel, respostaJson } from "@/lib/tracking/painel.server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Links da loja e o token de CSRF para criar e arquivar (#563).
export async function GET(request: NextRequest) {
  const pedido = await pedidoDoPainel(request, request.nextUrl.searchParams.get("seller"));
  if (!pedido.ok) return pedido.response;
  try {
    const lista = await listDomainCampaignLinks(pedido.sessionId, pedido.session, pedido.sellerId);
    return respostaJson({
      links: Array.isArray(lista.links) ? lista.links : [],
      csrf: csrfTokenForSession(pedido.sessionId, CAMPAIGN_LINK_CSRF_ACTION)
    });
  } catch (error) {
    return mappedError(error);
  }
}
