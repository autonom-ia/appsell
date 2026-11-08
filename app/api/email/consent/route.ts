import { NextRequest, NextResponse } from "next/server";

import {
  authenticatedSession,
  canonicalUuid,
  mappedError
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { ACAO_CSRF_DO_CONSENTIMENTO } from "@/lib/email-consent/texto";
import { recordDomainEmailConsent } from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import { verifyMutation } from "@/lib/mutation-protection.server";
import { baseDeLeadsLiberada } from "@/lib/reports/leads-pilot";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LIMITE_DO_CORPO = 1_024;
const ORIGENS = new Set(["checkout", "account"]);

function vazio(status: number) {
  return new NextResponse(null, { status, headers: { "cache-control": "no-store" } });
}

// Grava o consentimento de e-mail da loja pela própria pessoa, logada (#619).
// Ponto único da caixa do checkout e da conta. O e-mail não vem do pedido: a
// API usa o da identidade da sessão. Exige a origem configurada e o token
// anti-CSRF da ação; loja fora do piloto responde 404.
export async function POST(request: NextRequest) {
  const autenticado = await authenticatedSession(request);
  if (!autenticado) return vazio(401);
  let form: FormData | null;
  try {
    form = await readLimitedForm(request, LIMITE_DO_CORPO);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) return vazio(413);
    form = null;
  }
  const token = form?.get("_csrf");
  if (!verifyMutation(request, autenticado.sessionId, ACAO_CSRF_DO_CONSENTIMENTO, typeof token === "string" ? token : null)) {
    return vazio(403);
  }
  const sellerBruto = form?.get("seller");
  const origem = form?.get("source");
  const sellerId = canonicalUuid(typeof sellerBruto === "string" ? sellerBruto : undefined);
  if (!sellerId || typeof origem !== "string" || !ORIGENS.has(origem)) return vazio(422);
  if (!baseDeLeadsLiberada(sellerId)) return vazio(404);
  try {
    await recordDomainEmailConsent(
      autenticado.sessionId,
      autenticado.session,
      sellerId,
      origem as "checkout" | "account"
    );
  } catch (error) {
    return mappedError(error);
  }
  return vazio(204);
}
