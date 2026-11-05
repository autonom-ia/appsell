import { COOKIE_ATRIBUICAO, lerAtribuicao, type Atribuicao } from "./attribution";
import { aceitouOTextoAtual, COOKIE_ESCOLHA } from "./consent";

interface LeitorDeCookies {
  get(nome: string): { value: string } | undefined;
}

// Atribuição que o servidor pode usar no início do checkout. Só com o "Aceitar"
// da versão atual do texto e com o cookie dentro da janela de 7 dias; em
// qualquer outro caso, null, e o checkout segue sem atribuição.
export function atribuicaoDoPedido(cookies: LeitorDeCookies, agora: number): Atribuicao | null {
  if (!aceitouOTextoAtual(cookies.get(COOKIE_ESCOLHA)?.value)) return null;
  return lerAtribuicao(cookies.get(COOKIE_ATRIBUICAO)?.value, agora);
}
