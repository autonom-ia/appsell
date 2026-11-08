import { lojaNaListaDePiloto } from "./pilot-list";

// O relatório de vendas é funcionalidade nova: entra em piloto por loja
// (AGENTS.md §4), desligado para todas até a loja estar na lista. A Hub2You
// fica fora até o Rodrigo liberar.
const VARIAVEL = "APPSELL_SALES_REPORT_PILOT_SELLER_IDS";

export function relatorioDeVendasLiberado(
  sellerId: string,
  env: Record<string, string | undefined> = process.env
): boolean {
  return lojaNaListaDePiloto(VARIAVEL, sellerId, env);
}
