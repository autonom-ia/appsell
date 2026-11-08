"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { TEXTO_DO_BANNER } from "./banner-text";
import {
  bannerAbertoAoMontar,
  cookiesAoEscolher,
  cookiesAoMontar,
  EVENTO_ABRIR_COOKIES
} from "./banner-logic";
import { serializarEscolha, type Escolha } from "./consent";

function agoraEmSegundos(): number {
  return Math.floor(Date.now() / 1000);
}

function gravar(cookies: readonly string[]): void {
  for (const cookie of cookies) document.cookie = cookie;
}

// Conta a escolha no Sell, sem dado pessoal (loja, versão do texto, escolha).
// Falha aqui não muda nada para a pessoa: a escolha já está no navegador.
function contarEscolha(sellerId: string, escolha: Escolha): void {
  const corpo = new URLSearchParams({ seller: sellerId, choice: serializarEscolha(escolha) });
  void fetch("/api/tracking/consent", { method: "POST", body: corpo, keepalive: true }).catch(() => undefined);
}

// O cookie não avisa quando muda: a assinatura não faz nada, e a leitura vale
// para a renderização. No servidor não há escolha para ler, e o banner não sai.
function assinarNada(): () => void {
  return () => undefined;
}

function semBannerNoServidor(): boolean {
  return false;
}

interface BannerDeCookiesProps {
  sellerId: string;
  politicaHref: string;
}

// Montado pela vitrine só quando a loja está no piloto (pilot.ts). Sem
// escolha, nada é gravado; fechar a página não conta como aceite. A lógica do
// que é gravado está em banner-logic.ts, com teste.
export function BannerDeCookies({ sellerId, politicaHref }: BannerDeCookiesProps) {
  const lerSemEscolha = useCallback(() => bannerAbertoAoMontar(sellerId, document.cookie), [sellerId]);
  const semEscolha = useSyncExternalStore(assinarNada, lerSemEscolha, semBannerNoServidor);
  const [escolheuAgora, setEscolheuAgora] = useState(false);
  const [reaberto, setReaberto] = useState(false);
  const aberto = reaberto || (semEscolha && !escolheuAgora);

  useEffect(() => {
    gravar(cookiesAoMontar(sellerId, document.cookie, window.location.search, agoraEmSegundos()));
    const abrir = () => setReaberto(true);
    window.addEventListener(EVENTO_ABRIR_COOKIES, abrir);
    return () => window.removeEventListener(EVENTO_ABRIR_COOKIES, abrir);
  }, [sellerId]);

  const escolher = useCallback((escolha: Escolha) => {
    gravar(cookiesAoEscolher(sellerId, escolha, window.location.search, agoraEmSegundos()));
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
