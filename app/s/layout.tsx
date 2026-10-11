import type { Metadata } from "next";
import Link from "next/link";
import { demoModeEnabled } from "@/lib/demo-config";

export const metadata: Metadata = {
  title: "Vitrine | Autonom.ia Sell",
  description: "Conheça produtos digitais publicados por produtores na Autonom.ia Sell."
};

export default function StorefrontLayout({ children }: { children: React.ReactNode }) {
  const loginHref = demoModeEnabled() ? "/auth/login?persona=buyer" : "/auth/login";
  return <div className="storefront-shell">
    <a className="skip-link" href="#conteudo-vitrine">Pular para o conteúdo</a>
    <header className="storefront-topbar">
      <Link className="storefront-brand" href="/" aria-label="Autonom.ia Sell — início">
        <span className="brand-mark" aria-hidden="true">a</span>
        <strong>sell<span>.</span></strong>
      </Link>
      <Link className="storefront-login" href={loginHref}>Entrar</Link>
    </header>
    {children}
    <footer className="storefront-footer">
      <span>Autonom.ia Sell</span>
      <p>Produtos digitais em espaços independentes.</p>
    </footer>
  </div>;
}
