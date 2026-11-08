// Banner de cookies e atribuição UTM (#563): funcionalidade nova, em piloto
// por loja (AGENTS.md §4), desligada para todas até a loja estar na lista. A
// Hub2You fica fora até o Rodrigo liberar. Nenhuma loja liga antes do OK da
// revisão jurídica do texto do banner.
const VARIAVEL = "APPSELL_UTM_PILOT_SELLER_IDS";

export function atribuicaoLiberada(
  sellerId: string,
  env: Record<string, string | undefined> = process.env
): boolean {
  const lista = env[VARIAVEL] ?? "";
  return lista.split(",").map((item) => item.trim()).filter(Boolean).includes(sellerId);
}
