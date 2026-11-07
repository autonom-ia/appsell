import "server-only";

import { NextRequest, NextResponse } from "next/server";

import {
  authenticatedSession,
  canonicalUuid
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { sessionCookieName } from "@/lib/cookies";
import { verifyMutation } from "@/lib/mutation-protection.server";

import { atribuicaoLiberada } from "./pilot";

// Ação do CSRF dos links (#563). O token é derivado da sessão e da ação pela
// mesma chave do resto do painel; a ação é nossa, nada muda na Plataforma.
export const CAMPAIGN_LINK_CSRF_ACTION = "POST:/dashboard/links";

type Sessao = NonNullable<Awaited<ReturnType<typeof authenticatedSession>>>;

export type PedidoDoPainel =
  | { ok: true; sessionId: string; session: Sessao["session"]; sellerId: string }
  | { ok: false; response: NextResponse };

export function respostaJson(corpo: unknown, status = 200) {
  return NextResponse.json(corpo, { status, headers: { "cache-control": "no-store" } });
}

// Sessão, loja válida e loja no piloto, nesta ordem. Fora do piloto: 404, como
// se a rota não existisse.
export async function pedidoDoPainel(
  request: NextRequest,
  seller: string | null
): Promise<PedidoDoPainel> {
  const autenticado = await authenticatedSession(request);
  if (!autenticado) return { ok: false, response: respostaJson({ error: "unauthorized" }, 401) };
  const sellerId = canonicalUuid(seller ?? undefined);
  if (!sellerId) return { ok: false, response: respostaJson({ error: "invalid_seller" }, 422) };
  if (!atribuicaoLiberada(sellerId)) {
    return { ok: false, response: respostaJson({ error: "not_found" }, 404) };
  }
  return { ok: true, sessionId: autenticado.sessionId, session: autenticado.session, sellerId };
}

export function mutacaoValida(request: NextRequest, token: FormDataEntryValue | null): boolean {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  return verifyMutation(
    request,
    sessionId,
    CAMPAIGN_LINK_CSRF_ACTION,
    typeof token === "string" ? token : null
  );
}
