import { NextRequest, NextResponse } from "next/server";

import {
  authenticatedSession,
  canonicalUuid
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { relatorioDeVendasLiberado } from "@/lib/reports/sales-pilot";
import { periodoValido } from "@/lib/reports/sales-period";

type Sessao = NonNullable<Awaited<ReturnType<typeof authenticatedSession>>>;

export function respostaJson(corpo: unknown, status = 200) {
  return NextResponse.json(corpo, { status, headers: { "cache-control": "no-store" } });
}

export type PedidoDeVendas =
  | {
      ok: true;
      sessionId: string;
      session: Sessao["session"];
      sellerId: string;
      period: { from: string; to: string };
    }
  | { ok: false; response: NextResponse };

// Sessão, loja no piloto e período válido, nesta ordem. A loja fora do piloto
// responde 404, como se a rota não existisse.
export async function pedidoDeVendas(request: NextRequest): Promise<PedidoDeVendas> {
  const autenticado = await authenticatedSession(request);
  if (!autenticado) return { ok: false, response: respostaJson({ error: "unauthorized" }, 401) };
  const parametros = request.nextUrl.searchParams;
  const sellerId = canonicalUuid(parametros.get("seller") ?? undefined);
  const period = periodoValido(parametros.get("from"), parametros.get("to"));
  if (!sellerId || !period) {
    return { ok: false, response: respostaJson({ error: "invalid_period" }, 422) };
  }
  if (!relatorioDeVendasLiberado(sellerId)) {
    return { ok: false, response: respostaJson({ error: "not_found" }, 404) };
  }
  return { ok: true, sessionId: autenticado.sessionId, session: autenticado.session, sellerId, period };
}
