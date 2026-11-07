import { NextRequest } from "next/server";

import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { readDomainSalesReport } from "@/lib/internal-api-client.server";

import { pedidoDeVendas, respostaJson } from "../_compartilhado";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Relatório de vendas da loja por dia e produto (#562). Só leitura.
export async function GET(request: NextRequest) {
  const pedido = await pedidoDeVendas(request);
  if (!pedido.ok) return pedido.response;
  try {
    const relatorio = await readDomainSalesReport(
      pedido.sessionId,
      pedido.session,
      pedido.sellerId,
      pedido.period
    );
    return respostaJson(relatorio);
  } catch (error) {
    return mappedError(error);
  }
}
