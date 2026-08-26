import { demoModeEnabled } from "@/lib/demo-config";

export const dynamic = "force-dynamic";

export default async function Home({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  const demoEnabled = demoModeEnabled();
  // Real product entry: identity SSO configured and the public demo personas
  // disabled. Local/demo runtimes keep the preview-flavored landing below.
  const identityMode = !demoEnabled && process.env.APPSELL_OAUTH_MODE === "identity";
  return (
    <div className="landing">
      <a className="skip-link" href="#inicio">Pular para o conteúdo</a>
      <header><nav className="landing-nav" aria-label="Navegação"><a className="landing-brand" href="#inicio" aria-label="Autonom.ia Sell — início"><span className="brand-mark" aria-hidden="true">a</span><strong>Autonom.ia <span>Sell</span></strong></a>{identityMode ? null : <span className="demo-pill">DEMO</span>}</nav></header>
      <main id="inicio" tabIndex={-1}>
      {error === "session_unavailable" ? <p className="landing-alert" role="alert"><strong>Sessão indisponível.</strong> {identityMode ? "O login não respondeu. Tente entrar novamente em alguns instantes." : "A demonstração não respondeu. Tente entrar novamente em alguns instantes."}</p> : null}
      <section className="hero">
        <div className="hero-copy"><p className="eyebrow">{identityMode ? "Autonom.ia Sell" : "Prévia da Autonom.ia Sell"}</p><h1>Seu conhecimento.<br/><span>Seu negócio.</span></h1><p className="hero-lead">{identityMode ? "Crie, publique e venda seus produtos digitais e entregue conteúdo aos seus alunos com a sua marca." : "Explore a experiência mobile para criar, publicar e consumir um curso dentro de uma loja isolada. Pagamentos e login comercial continuam fora desta demonstração."}</p><div className="hero-actions">{identityMode ? <a className="primary-button" href="/auth/login">Entrar na sua conta <span aria-hidden="true">→</span></a> : demoEnabled ? <><a className="primary-button" href="/auth/login?persona=admin">Entrar como admin demo <span aria-hidden="true">→</span></a><a className="secondary-button" href="/auth/login?persona=buyer">Entrar como aluno demo</a><p>Contas demo: admin.demo@autonomia.site e user.demo@autonomia.site. Nenhuma senha é armazenada no código.</p></> : <a className="primary-button" href="/auth/login">Entrar no ambiente local <span aria-hidden="true">→</span></a>}</div></div>
        <div className="hero-visual" aria-hidden="true"><div className="visual-glow"/><div className="phone-card"><div className="phone-top"><span className="mini-mark">a</span><span>Visão geral</span><i/></div><p>Olá, produtor.</p><h2>Seu negócio em movimento.</h2>{identityMode ? <div className="mini-metrics"><span><small>Produtos</small><strong>—</strong></span><span><small>Alunos</small><strong>—</strong></span></div> : <div className="mini-metrics"><span><small>Produtos demo</small><strong>03</strong></span><span><small>Alunos demo</small><strong>128</strong></span></div>}{identityMode ? <div className="mini-product"><i/><span><strong>Seu primeiro curso</strong><small>começa aqui</small></span><b>→</b></div> : <div className="mini-product"><i/><span><strong>Curso demonstrativo</strong><small>42% em andamento</small></span><b>→</b></div>}</div></div>
      </section>
        <section className="trust-strip" aria-label={identityMode ? "Destaques" : "Estado da prévia"}><span>Interface mobile-first</span><span>Separação entre lojas verificada</span><span>Sessão protegida no servidor</span></section>
      </main>
    </div>
  );
}
