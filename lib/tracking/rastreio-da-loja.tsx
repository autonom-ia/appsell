"use client";

import { abrirPreferenciasDeCookies } from "./banner-logic";
import { BannerDeCookies } from "./consent-banner";

interface RastreioDaLojaProps {
  sellerId: string;
}

// O que a vitrine monta quando a loja está no piloto (#563): o banner, que só
// aparece no navegador e enquanto não houver escolha, e o link "Cookies" no
// rodapé, que reabre o banner. Fora do piloto, a vitrine não monta nada disto.
export function RastreioDaLoja({ sellerId }: RastreioDaLojaProps) {
  return (
    <>
      <BannerDeCookies sellerId={sellerId} politicaHref="/cookies"/>
      <p className="storefront-footer">
        <button type="button" className="secondary-button" onClick={() => abrirPreferenciasDeCookies()}>
          Cookies
        </button>
      </p>
    </>
  );
}
