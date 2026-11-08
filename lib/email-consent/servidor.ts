import "server-only";

import { csrfTokenForSession } from "@/lib/mutation-protection.server";
import { baseDeLeadsLiberada } from "@/lib/reports/leads-pilot";

import { ACAO_CSRF_DO_CONSENTIMENTO } from "./texto";

// Para a página (checkout ou "Minha conta") que mostra a caixa de
// consentimento de e-mail da loja (#619). Fora do piloto da base de leads,
// devolve null, e a página não mostra a caixa.
export function propsDaCaixaDeConsentimento(
  sessionId: string,
  sellerId: string,
  env: Record<string, string | undefined> = process.env
): { sellerId: string; csrf: string } | null {
  if (!baseDeLeadsLiberada(sellerId, env)) return null;
  return { sellerId, csrf: csrfTokenForSession(sessionId, ACAO_CSRF_DO_CONSENTIMENTO) };
}
