// O que o banner grava no navegador, separado do React para ter teste
// (#563). Cada função devolve os cookies a gravar, na ordem; o componente só
// os atribui a `document.cookie`. Nada aqui grava sozinho.

import {
  atribuicaoDaUrl,
  cookieDeAtribuicao,
  JANELA_SEGUNDOS,
  serializarAtribuicao
} from "./attribution";
import {
  aceitouOTextoAtual,
  cookieDeEscolha,
  DURACAO_DA_ESCOLHA_SEGUNDOS,
  precisaPerguntar,
  serializarEscolha,
  type Escolha
} from "./consent";
import { apagarCookie, cookieDoNavegador, valorDoCookie } from "./cookies";

// Evento que reabre o banner: o botão "Cookies" do rodapé o dispara.
export const EVENTO_ABRIR_COOKIES = "sell:abrir-cookies";

function capturar(sellerId: string, busca: string, agora: number): string[] {
  const atribuicao = atribuicaoDaUrl(new URLSearchParams(busca), agora);
  if (atribuicao === null) return [];
  return [cookieDoNavegador(cookieDeAtribuicao(sellerId), serializarAtribuicao(atribuicao), JANELA_SEGUNDOS)];
}

// Ao abrir a página: com o "Aceitar" da versão atual nesta loja, grava a
// visita com UTM; sem ele, nada.
export function cookiesAoMontar(sellerId: string, cabecalho: string, busca: string, agora: number): string[] {
  return aceitouOTextoAtual(valorDoCookie(cabecalho, cookieDeEscolha(sellerId)))
    ? capturar(sellerId, busca, agora)
    : [];
}

export function bannerAbertoAoMontar(sellerId: string, cabecalho: string): boolean {
  return precisaPerguntar(valorDoCookie(cabecalho, cookieDeEscolha(sellerId)));
}

// Ao escolher: grava a escolha; "Aceitar" grava a visita desta página, e
// "Recusar" apaga a atribuição desta loja na hora.
export function cookiesAoEscolher(sellerId: string, escolha: Escolha, busca: string, agora: number): string[] {
  const daEscolha = cookieDoNavegador(cookieDeEscolha(sellerId), serializarEscolha(escolha), DURACAO_DA_ESCOLHA_SEGUNDOS);
  return escolha === "accepted"
    ? [daEscolha, ...capturar(sellerId, busca, agora)]
    : [daEscolha, apagarCookie(cookieDeAtribuicao(sellerId))];
}

export function abrirPreferenciasDeCookies(alvo: EventTarget = window): void {
  alvo.dispatchEvent(new Event(EVENTO_ABRIR_COOKIES));
}
