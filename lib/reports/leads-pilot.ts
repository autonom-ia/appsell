import { lojaNaListaDePiloto } from "./pilot-list";

// Base de leads e caixa de consentimento de e-mail da loja (#619): piloto por
// loja numa env própria, vazia por padrão. A Orquestração liga depois do
// deploy. Fora da lista, a tela e as rotas respondem 404, e a caixa não
// aparece no checkout nem na conta.
const VARIAVEL = "APPSELL_LEADS_PILOT_SELLER_IDS";

export function baseDeLeadsLiberada(
  sellerId: string,
  env: Record<string, string | undefined> = process.env
): boolean {
  return lojaNaListaDePiloto(VARIAVEL, sellerId, env);
}
