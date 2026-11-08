import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { cookieDeAtribuicao, JANELA_SEGUNDOS, lerAtribuicao } from "@/lib/tracking/attribution";
import {
  abrirPreferenciasDeCookies,
  bannerAbertoAoMontar,
  cookiesAoEscolher,
  cookiesAoMontar,
  EVENTO_ABRIR_COOKIES
} from "@/lib/tracking/banner-logic";
import { BannerDeCookies } from "@/lib/tracking/consent-banner";
import { cookieDeEscolha, serializarEscolha } from "@/lib/tracking/consent";
import { criarLimite } from "@/lib/tracking/limite";
import { RastreioDaLoja } from "@/lib/tracking/rastreio-da-loja";

const LOJA = "44444444-4444-4444-8444-444444444444";
const OUTRA = "55555555-5555-4555-8555-555555555555";
const AGORA = 1_793_000_000;
const COM_UTM = "?utm_source=insta&utm_campaign=out";

function nomeEValor(cookie: string): [string, string] {
  const [par] = cookie.split(";");
  const igual = par!.indexOf("=");
  return [par!.slice(0, igual), par!.slice(igual + 1)];
}

describe("o que o banner grava", () => {
  it("antes de qualquer clique, sem escolha, nada é gravado e o banner abre", () => {
    expect(cookiesAoMontar(LOJA, "", COM_UTM, AGORA)).toEqual([]);
    expect(bannerAbertoAoMontar(LOJA, "")).toBe(true);
  });

  it("a recusa guardada não grava nada e não pergunta de novo", () => {
    const cabecalho = `${cookieDeEscolha(LOJA)}=${serializarEscolha("refused")}`;
    expect(cookiesAoMontar(LOJA, cabecalho, COM_UTM, AGORA)).toEqual([]);
    expect(bannerAbertoAoMontar(LOJA, cabecalho)).toBe(false);
  });

  it("o aceite de outra loja não vale aqui", () => {
    const cabecalho = `${cookieDeEscolha(OUTRA)}=${serializarEscolha("accepted")}`;
    expect(cookiesAoMontar(LOJA, cabecalho, COM_UTM, AGORA)).toEqual([]);
    expect(bannerAbertoAoMontar(LOJA, cabecalho)).toBe(true);
  });

  it("com o aceite desta loja, a visita com UTM é gravada no cookie desta loja", () => {
    const cabecalho = `${cookieDeEscolha(LOJA)}=${serializarEscolha("accepted")}`;
    const gravados = cookiesAoMontar(LOJA, cabecalho, COM_UTM, AGORA);
    expect(gravados).toHaveLength(1);
    const [nome, valor] = nomeEValor(gravados[0]!);
    expect(nome).toBe(cookieDeAtribuicao(LOJA));
    expect(gravados[0]).toContain(`Max-Age=${JANELA_SEGUNDOS}`);
    expect(lerAtribuicao(valor, AGORA)?.utm).toEqual({ utm_source: "insta", utm_campaign: "out" });
    expect(cookiesAoMontar(LOJA, cabecalho, "?q=curso", AGORA)).toEqual([]);
  });

  it("Aceitar grava a escolha e a visita; Recusar grava a escolha e apaga o sell_utm desta loja", () => {
    const aceitar = cookiesAoEscolher(LOJA, "accepted", COM_UTM, AGORA);
    expect(aceitar.map((cookie) => nomeEValor(cookie)[0])).toEqual([cookieDeEscolha(LOJA), cookieDeAtribuicao(LOJA)]);
    const recusar = cookiesAoEscolher(LOJA, "refused", COM_UTM, AGORA);
    expect(recusar.map((cookie) => nomeEValor(cookie)[0])).toEqual([cookieDeEscolha(LOJA), cookieDeAtribuicao(LOJA)]);
    expect(nomeEValor(recusar[1]!)[1]).toBe("");
    expect(recusar[1]).toContain("Max-Age=0");
  });

  it("o botão Cookies dispara o evento que reabre o aviso", () => {
    const alvo = new EventTarget();
    let reabertos = 0;
    alvo.addEventListener(EVENTO_ABRIR_COOKIES, () => { reabertos += 1; });
    abrirPreferenciasDeCookies(alvo);
    expect(reabertos).toBe(1);
  });

  it("no servidor o banner não sai; o rastreio da loja só traz o botão Cookies", () => {
    expect(renderToStaticMarkup(createElement(BannerDeCookies, { sellerId: LOJA, politicaHref: "/cookies" }))).toBe("");
    const html = renderToStaticMarkup(createElement(RastreioDaLoja, { sellerId: LOJA }));
    expect(html).toContain(">Cookies</button>");
    expect(html).not.toContain("sell-cookies-titulo");
  });
});

describe("limite por IP", () => {
  it("passando do teto de chaves, sai a mais antiga e o limite das outras fica", () => {
    const limite = criarLimite(2, 60_000, 3);
    expect(limite.dentro("a", 0)).toBe(true);
    expect(limite.dentro("a", 1)).toBe(true);
    expect(limite.dentro("a", 2)).toBe(false);
    limite.dentro("b", 3);
    limite.dentro("c", 4);
    limite.dentro("d", 5);
    expect(limite.tamanhoParaTeste()).toBe(3);
    // "a" era a mais antiga e saiu; "b" continua contada.
    expect(limite.dentro("b", 6)).toBe(true);
    expect(limite.dentro("b", 7)).toBe(false);
  });

  it("janela vencida recomeça a contagem", () => {
    const limite = criarLimite(1, 1_000);
    expect(limite.dentro("x", 0)).toBe(true);
    expect(limite.dentro("x", 10)).toBe(false);
    expect(limite.dentro("x", 1_000)).toBe(true);
  });
});
