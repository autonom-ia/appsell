import { NextRequest, NextResponse } from "next/server";

import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { exportDomainSalesRows } from "@/lib/internal-api-client.server";
import { linhasDeVendaEmCsv, type LinhaDeVenda } from "@/lib/reports/sales-csv";

import { pedidoDeVendas, respostaJson } from "../_compartilhado";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// CSV das vendas (#562): uma linha por compra, sem dado pessoal. A API audita
// cada export e limita a 5 por minuto.
export async function GET(request: NextRequest) {
  const pedido = await pedidoDeVendas(request);
  if (!pedido.ok) return pedido.response;
  try {
    const exportado = await exportDomainSalesRows(
      pedido.sessionId,
      pedido.session,
      pedido.sellerId,
      pedido.period
    );
    const linhas = Array.isArray(exportado.rows) ? (exportado.rows as LinhaDeVenda[]) : null;
    if (!linhas) return respostaJson({ error: "sales_report_unavailable" }, 503);
    return new NextResponse(linhasDeVendaEmCsv(linhas), {
      status: 200,
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition":
          `attachment; filename="vendas-${pedido.period.from}-a-${pedido.period.to}.csv"`,
        "cache-control": "no-store",
        "x-content-type-options": "nosniff"
      }
    });
  } catch (error) {
    return mappedError(error);
  }
}
