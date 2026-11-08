import { NextRequest, NextResponse } from "next/server";

import {
  authenticatedSession,
  canonicalUuid
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { baseDeLeadsLiberada } from "@/lib/reports/leads-pilot";
import { periodoValido } from "@/lib/reports/sales-period";

type Sessao = NonNullable<Awaited<ReturnType<typeof authenticatedSession>>>;

export function respostaJson(corpo: unknown, status = 200) {
  return NextResponse.json(corpo, { status, headers: { "cache-control": "no-store" } });
}

export type PedidoDeLeads =
  | {
      ok: true;
      sessionId: string;
      session: Sessao["session"];
      sellerId: string;
      filter: { from: string; to: string; product: string | null };
    }
  | { ok: false; response: NextResponse };

// Sessão, loja no piloto, período e produto válidos, nesta ordem. A loja fora
// do piloto responde 404, como se a rota não existisse.
export async function pedidoDeLeads(request: NextRequest): Promise<PedidoDeLeads> {
  const autenticado = await authenticatedSession(request);
  if (!autenticado) return { ok: false, response: respostaJson({ error: "unauthorized" }, 401) };
  const parametros = request.nextUrl.searchParams;
  const sellerId = canonicalUuid(parametros.get("seller") ?? undefined);
  const period = periodoValido(parametros.get("from"), parametros.get("to"));
  const produtoBruto = parametros.get("product");
  const product = produtoBruto === null ? null : canonicalUuid(produtoBruto);
  if (!sellerId || !period || (produtoBruto !== null && product === null)) {
    return { ok: false, response: respostaJson({ error: "invalid_filter" }, 422) };
  }
  if (!baseDeLeadsLiberada(sellerId)) {
    return { ok: false, response: respostaJson({ error: "not_found" }, 404) };
  }
  return {
    ok: true,
    sessionId: autenticado.sessionId,
    session: autenticado.session,
    sellerId,
    filter: { ...period, product }
  };
}
