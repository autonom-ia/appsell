// Pedido pinado (#154): funcionalidade nova de dinheiro, em piloto por loja
// (AGENTS.md §4), desligada para todas até a loja estar na lista. A Hub2You
// fica fora até o Rodrigo liberar.
const VARIAVEL = "APPSELL_PINNED_ORDER_SELLER_IDS";

export function pedidoPinadoLiberado(
  sellerId: string,
  env: Record<string, string | undefined> = process.env
): boolean {
  const lista = env[VARIAVEL] ?? "";
  return lista
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean)
    .includes(sellerId.toLowerCase());
}
