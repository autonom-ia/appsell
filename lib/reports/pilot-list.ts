// Lista de piloto por loja (AGENTS.md §4): ids de loja separados por vírgula
// numa env. Vazia ou ausente, nenhuma loja entra.
export function lojaNaListaDePiloto(
  variavel: string,
  sellerId: string,
  env: Record<string, string | undefined> = process.env
): boolean {
  const lista = env[variavel] ?? "";
  return lista.split(",").map((item) => item.trim()).filter(Boolean).includes(sellerId);
}
