import { NextRequest } from "next/server";

import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { readDomainCampaignSales } from "@/lib/internal-api-client.server";
import { periodoValido } from "@/lib/tracking/period";
import { pedidoDoPainel, respostaJson } from "@/lib/tracking/painel.server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Vendas por campanha no período (#563): só as vendas com atribuição.
export async function GET(request: NextRequest) {
  const parametros = request.nextUrl.searchParams;
  const pedido = await pedidoDoPainel(request, parametros.get("seller"));
  if (!pedido.ok) return pedido.response;
  const period = periodoValido(parametros.get("from"), parametros.get("to"));
  if (!period) return respostaJson({ error: "invalid_period" }, 422);
  try {
    const relatorio = await readDomainCampaignSales(
      pedido.sessionId,
      pedido.session,
      pedido.sellerId,
      period
    );
    return respostaJson({ rows: Array.isArray(relatorio.rows) ? relatorio.rows : [] });
  } catch (error) {
    return mappedError(error);
  }
}
