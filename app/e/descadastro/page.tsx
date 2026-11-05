import type { Metadata } from "next";
import Link from "next/link";

import { ConfirmarDescadastro } from "./confirmar";
import "../../legal.css";

// O token vem no fragmento (#t=...), que o navegador não manda ao servidor:
// não chega ao log de acesso nem ao Referer. Esta página não tem nada da
// pessoa, então pode ser estática; o descadastro é o POST do botão.
export const metadata: Metadata = {
  title: "Parar de receber e-mails · Autonom.ia Sell",
  robots: { index: false, follow: false },
  referrer: "no-referrer"
};

export default function Pagina() {
  return (
    <div className="legal">
      <header className="legal-topo">
        <Link className="legal-marca" href="/">
          <span className="ico" />
          <span><b>Autonom.ia</b><small>Sell</small></span>
        </Link>
      </header>
      <main className="legal-corpo">
        <p className="legal-eyebrow">Preferências de e-mail</p>
        <h1>Parar de receber estes e-mails</h1>
        <div className="legal-texto">
          <p>
            Você deixa de receber os e-mails opcionais deste remetente, como novidades e
            lembretes. Os e-mails da sua compra e do seu acesso continuam chegando, porque
            sem eles você perderia o que comprou.
          </p>
          <ConfirmarDescadastro />
        </div>
      </main>
    </div>
  );
}
