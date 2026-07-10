"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

interface AppShellProps {
  children: ReactNode;
}

const navigation = [
  { href: "/dashboard", label: "Visão geral", mark: "home" },
  { href: "/products", label: "Produtos", mark: "products" },
  { href: "/library", label: "Minha biblioteca", mark: "library" },
] as const;

function NavIcon({ name }: { name: (typeof navigation)[number]["mark"] }) {
  const paths = {
    home: <><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-7h6v7"/></>,
    products: <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 4v16M8 9h13"/></>,
    library: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();

  return (
    <div className="app-shell">
      <a className="skip-link" href="#conteudo">Pular para o conteúdo</a>
      <aside className="app-sidebar" aria-label="Navegação principal">
        <Link className="brand" href="/dashboard" aria-label="Autonom.ia Sell — início">
          <span className="brand-mark" aria-hidden="true">a</span>
          <span><strong>sell<span>.</span></strong><small>by Autonom.ia</small></span>
        </Link>
        <nav>
          <p className="nav-eyebrow">Seu negócio</p>
          {navigation.map((item) => {
            const active = pathname === item.href;
            return <Link key={item.href} href={item.href} className={active ? "nav-link active" : "nav-link"} aria-current={active ? "page" : undefined}><NavIcon name={item.mark}/><span>{item.label}</span></Link>;
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="avatar" aria-hidden="true">AU</span>
          <span><strong>Ambiente demo</strong><small>Sessão protegida</small></span>
        </div>
        <form action="/auth/logout" method="post"><button className="logout-button" type="submit">Sair com segurança</button></form>
      </aside>
      <div className="app-content">
        <header className="mobile-topbar">
          <Link className="mobile-brand" href="/dashboard"><span className="brand-mark" aria-hidden="true">a</span><strong>sell<span>.</span></strong></Link>
          <form action="/auth/logout" method="post"><button className="mobile-logout" type="submit" aria-label="Sair com segurança">Sair</button></form>
        </header>
        <div className="environment-banner" role="status"><strong>Prévia local, não é grant.</strong> Dados demonstrativos; publicação, cobrança e integrações reais estão desativadas.</div>
        <main id="conteudo" className="app-main" tabIndex={-1}>{children}</main>
        <nav className="bottom-nav" aria-label="Navegação móvel">
          {navigation.map((item) => {
            const active = pathname === item.href;
            return <Link key={item.href} href={item.href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}><NavIcon name={item.mark}/><span>{item.label}</span></Link>;
          })}
        </nav>
      </div>
    </div>
  );
}
