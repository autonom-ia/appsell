"use client";

import { useCallback, useEffect, useState } from "react";

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
  politicaHref: string;
}

// Montado pela vitrine só quando a loja está no piloto (pilot.ts). Sem
// escolha, nada é gravado; fechar a página não conta como aceite.
export function BannerDeCookies({ politicaHref }: BannerDeCookiesProps) {
  const [aberto, setAberto] = useState(false);

  useEffect(() => {
    const escolha = valorDoCookie(document.cookie, COOKIE_ESCOLHA);
    if (aceitouOTextoAtual(escolha)) capturarDaUrl();
    setAberto(precisaPerguntar(escolha));
    const abrir = () => setAberto(true);
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
    setAberto(false);
  }, []);

  if (!aberto) return null;
  return (
    <section
      className="panel"
      role="region"
      aria-labelledby="sell-cookies-titulo"
      style={{ position: "fixed", insetInline: 16, bottom: 16, zIndex: 50, maxWidth: 640, marginInline: "auto" }}
    >
      <h2 id="sell-cookies-titulo">{TEXTO_DO_BANNER.titulo}</h2>
      <p>
        {TEXTO_DO_BANNER.corpo} <a href={politicaHref}>{TEXTO_DO_BANNER.politica}</a>
      </p>
      <div className="form-row">
        <button type="button" className="button" onClick={() => escolher("accepted")}>
          {TEXTO_DO_BANNER.aceitar}
        </button>
        <button type="button" className="button" onClick={() => escolher("refused")}>
          {TEXTO_DO_BANNER.recusar}
        </button>
      </div>
    </section>
  );
}
