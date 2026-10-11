"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

export default function StorefrontError({ reset }: { reset: () => void }) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { titleRef.current?.focus(); }, []);
  return <main id="conteudo-vitrine" className="storefront-state" tabIndex={-1}>
    <p className="eyebrow">Vitrine indisponível</p>
    <h1 ref={titleRef} tabIndex={-1}>Não foi possível carregar esta vitrine.</h1>
    <p role="alert">A conexão falhou antes de confirmar o catálogo público. Nenhum conteúdo privado foi exibido.</p>
    <div><button className="primary-button" type="button" onClick={reset}>Tentar novamente</button><Link className="secondary-button" href="/">Ir para o início</Link></div>
  </main>;
}
