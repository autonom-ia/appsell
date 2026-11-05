// Escolha da pessoa sobre o cookie de atribuição (#563, decisão do Rodrigo em
// 05/11/2026, opção B). A recusa é o padrão: sem escolha guardada, nada é
// gravado. A escolha vale para a versão do texto do banner; texto novo pede a
// escolha de novo.

export const COOKIE_ESCOLHA = "sell_cookie_choice";
export const VERSAO_DO_TEXTO = "v1";
export const DURACAO_DA_ESCOLHA_SEGUNDOS = 183 * 24 * 60 * 60;

export type Escolha = "accepted" | "refused";

const ESCOLHAS: ReadonlySet<string> = new Set<Escolha>(["accepted", "refused"]);
const SEPARADOR = ".";

export function serializarEscolha(escolha: Escolha, versao: string = VERSAO_DO_TEXTO): string {
  return `${versao}${SEPARADOR}${escolha}`;
}

export function lerEscolha(valor: string | undefined): { versao: string; escolha: Escolha } | null {
  if (valor === undefined) return null;
  const partes = valor.split(SEPARADOR);
  if (partes.length !== 2) return null;
  const [versao, escolha] = partes as [string, string];
  if (versao.length === 0 || !ESCOLHAS.has(escolha)) return null;
  return { versao, escolha: escolha as Escolha };
}

// Só o "Aceitar" da versão atual do texto libera a gravação.
export function aceitouOTextoAtual(valor: string | undefined): boolean {
  const lida = lerEscolha(valor);
  return lida !== null && lida.versao === VERSAO_DO_TEXTO && lida.escolha === "accepted";
}

// O banner aparece enquanto não houver escolha para a versão atual.
export function precisaPerguntar(valor: string | undefined): boolean {
  const lida = lerEscolha(valor);
  return lida === null || lida.versao !== VERSAO_DO_TEXTO;
}
