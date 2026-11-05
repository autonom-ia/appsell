import { cookieDeAtribuicao, lerAtribuicao, type Atribuicao } from "./attribution";
import { cookieDeEscolha, versaoAceita } from "./consent";

interface LeitorDeCookies {
  get(nome: string): { value: string } | undefined;
}

export interface AtribuicaoAceita {
  atribuicao: Atribuicao;
  versaoDoTexto: string;
}

// Atribuição que o servidor pode usar no início do checkout da loja. Só com o
// "Aceitar" da versão atual do texto naquela loja e com o cookie dela dentro
// da janela de 7 dias; em qualquer outro caso, null, e o checkout segue sem
// atribuição. A versão aceita vai junto, para provar qual texto a pessoa viu.
export function atribuicaoDoPedido(
  cookies: LeitorDeCookies,
  sellerId: string,
  agora: number
): AtribuicaoAceita | null {
  const versaoDoTexto = versaoAceita(cookies.get(cookieDeEscolha(sellerId))?.value);
  if (versaoDoTexto === null) return null;
  const atribuicao = lerAtribuicao(cookies.get(cookieDeAtribuicao(sellerId))?.value, agora);
  return atribuicao === null ? null : { atribuicao, versaoDoTexto };
}
