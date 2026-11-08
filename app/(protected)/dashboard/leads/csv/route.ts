import { NextRequest, NextResponse } from "next/server";

import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { exportDomainStoreLeads } from "@/lib/internal-api-client.server";
import { linhasDeLeadEmCsv, type LinhaDeLead } from "@/lib/reports/leads-csv";

import { pedidoDeLeads, respostaJson } from "../_compartilhado";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// CSV "para marketing" da base de leads (#619): só quem pode receber e-mail
// opcional da loja agora. A API audita cada export e limita a 5 por minuto.
//
// O GET gasta a cota e grava auditoria: pedido que o navegador marca como
// vindo de outro site é recusado antes de tudo, como no CSV de vendas.
const ORIGENS_RECUSADAS = new Set(["cross-site", "same-site"]);

export async function GET(request: NextRequest) {
  if (ORIGENS_RECUSADAS.has(request.headers.get("sec-fetch-site") ?? "")) {
    return respostaJson({ error: "forbidden" }, 403);
  }
  const pedido = await pedidoDeLeads(request);
  if (!pedido.ok) return pedido.response;
  try {
    const exportado = await exportDomainStoreLeads(pedido.sessionId, pedido.session, pedido.sellerId, pedido.filter);
    const linhas = Array.isArray(exportado.rows) ? (exportado.rows as LinhaDeLead[]) : null;
    if (!linhas) return respostaJson({ error: "leads_unavailable" }, 503);
    return new NextResponse(linhasDeLeadEmCsv(linhas), {
      status: 200,
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition":
          `attachment; filename="leads-marketing-${pedido.filter.from}-a-${pedido.filter.to}.csv"`,
        "cache-control": "no-store",
        "x-content-type-options": "nosniff"
      }
    });
  } catch (error) {
    return mappedError(error);
  }
}
