"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import {
  atribuicaoDaUrl,
  COOKIE_ATRIBUICAO,
  JANELA_SEGUNDOS,
  serializarAtribuicao
} from "./attribution";
import { TEXTO_DO_BANNER } from "./banner-text";
import {
  aceitouOTextoAtual,
  COOKIE_ESCOLHA,
  DURACAO_DA_ESCOLHA_SEGUNDOS,
  precisaPerguntar,
  serializarEscolha,
  type Escolha
} from "./consent";
import { apagarCookie, cookieDoNavegador, valorDoCookie } from "./cookies";

// Evento que reabre o banner: o link "Cookies" do rodapé da vitrine dispara.
export const EVENTO_ABRIR_COOKIES = "sell:abrir-cookies";

export function abrirPreferenciasDeCookies(): void {
  window.dispatchEvent(new Event(EVENTO_ABRIR_COOKIES));
}

function agoraEmSegundos(): number {
  return Math.floor(Date.now() / 1000);
}

// Grava a atribuição só se a página veio com UTM ou link da loja. Vale a
// última visita: uma nova substitui a anterior.
function capturarDaUrl(): void {
  const atribuicao = atribuicaoDaUrl(new URLSearchParams(window.location.search), agoraEmSegundos());
  if (atribuicao === null) return;
  document.cookie = cookieDoNavegador(
    COOKIE_ATRIBUICAO,
    serializarAtribuicao(atribuicao),
    JANELA_SEGUNDOS
  );
}

interface BannerDeCookiesProps {
  sellerId: string;
  politicaHref: string;
}

// Conta a escolha no Sell, sem dado pessoal (loja, versão do texto, escolha).
// Falha aqui não muda nada para a pessoa: a escolha já está no navegador.
function contarEscolha(sellerId: string, escolha: Escolha): void {
  const corpo = new URLSearchParams({ seller: sellerId, choice: serializarEscolha(escolha) });
  void fetch("/api/tracking/consent", { method: "POST", body: corpo, keepalive: true }).catch(() => undefined);
}

// Montado pela vitrine só quando a loja está no piloto (pilot.ts). Sem
// escolha, nada é gravado; fechar a página não conta como aceite.
// O cookie não avisa quando muda: a assinatura não faz nada, e a leitura vale
// para a renderização. No servidor não há escolha para ler, e o banner não sai.
function assinarNada(): () => void {
  return () => undefined;
}

function precisaPerguntarAgora(): boolean {
  return precisaPerguntar(valorDoCookie(document.cookie, COOKIE_ESCOLHA));
}

function semBannerNoServidor(): boolean {
  return false;
}

export function BannerDeCookies({ sellerId, politicaHref }: BannerDeCookiesProps) {
  const semEscolha = useSyncExternalStore(assinarNada, precisaPerguntarAgora, semBannerNoServidor);
  const [escolheuAgora, setEscolheuAgora] = useState(false);
  const [reaberto, setReaberto] = useState(false);
  const aberto = reaberto || (semEscolha && !escolheuAgora);

  useEffect(() => {
    if (aceitouOTextoAtual(valorDoCookie(document.cookie, COOKIE_ESCOLHA))) capturarDaUrl();
    const abrir = () => setReaberto(true);
    window.addEventListener(EVENTO_ABRIR_COOKIES, abrir);
    return () => window.removeEventListener(EVENTO_ABRIR_COOKIES, abrir);
  }, []);

  const escolher = useCallback((escolha: Escolha) => {
    document.cookie = cookieDoNavegador(
      COOKIE_ESCOLHA,
      serializarEscolha(escolha),
      DURACAO_DA_ESCOLHA_SEGUNDOS
    );
    if (escolha === "accepted") capturarDaUrl();
    else document.cookie = apagarCookie(COOKIE_ATRIBUICAO);
    contarEscolha(sellerId, escolha);
    setEscolheuAgora(true);
    setReaberto(false);
  }, [sellerId]);

  if (!aberto) return null;
  return (
    <section
      className="storefront-cookie-banner"
      role="region"
      aria-labelledby="sell-cookies-titulo"
      style={{
        position: "fixed", insetInline: 16, bottom: 16, zIndex: 50, maxWidth: 640, marginInline: "auto",
        padding: 16, borderRadius: 12, background: "Canvas", color: "CanvasText",
        boxShadow: "0 8px 24px rgb(0 0 0 / 0.18)"
      }}
    >
      <h2 id="sell-cookies-titulo">{TEXTO_DO_BANNER.titulo}</h2>
      <p>
        {TEXTO_DO_BANNER.corpo} <a href={politicaHref}>{TEXTO_DO_BANNER.politica}</a>
      </p>
      <div className="form-row">
        <button type="button" className="secondary-button" onClick={() => escolher("accepted")}>
          {TEXTO_DO_BANNER.aceitar}
        </button>
        <button type="button" className="secondary-button" onClick={() => escolher("refused")}>
          {TEXTO_DO_BANNER.recusar}
        </button>
      </div>
    </section>
  );
}
