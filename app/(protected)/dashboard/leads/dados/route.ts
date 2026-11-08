import { NextRequest } from "next/server";

import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { readDomainStoreLeads } from "@/lib/internal-api-client.server";

import { pedidoDeLeads, respostaJson } from "../_compartilhado";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Base de leads da loja (#619). Só leitura; a resposta tem e-mail e não fica
// em cache.
export async function GET(request: NextRequest) {
  const pedido = await pedidoDeLeads(request);
  if (!pedido.ok) return pedido.response;
  try {
    const leads = await readDomainStoreLeads(pedido.sessionId, pedido.session, pedido.sellerId, pedido.filter);
    return respostaJson(leads);
  } catch (error) {
    return mappedError(error);
  }
}
