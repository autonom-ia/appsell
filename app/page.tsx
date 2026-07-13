import { demoModeEnabled } from "@/lib/demo-config";

export default async function Home({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  const demoEnabled = demoModeEnabled();
  return (
    <main className="landing">
      <nav className="landing-nav" aria-label="Navegação"><a className="landing-brand" href="#inicio"><span className="brand-mark" aria-hidden="true">a</span><strong>sell<span>.</span></strong></a><span className="demo-pill">DEMO LOCAL</span></nav>
      {error === "session_unavailable" ? <p className="landing-alert" role="alert"><strong>Sessão indisponível.</strong> O ambiente local não respondeu. Tente entrar novamente em alguns instantes.</p> : null}
      <section id="inicio" className="hero">
        <div className="hero-copy"><p className="eyebrow">Prévia da Autonom.ia Sell</p><h1>Seu conhecimento.<br/><span>Seu negócio.</span></h1><p className="hero-lead">Explore a experiência mobile para criar, publicar e consumir um curso dentro de uma tenant isolada. Cobrança e Identity real continuam fora desta demonstração.</p><div className="hero-actions">{demoEnabled ? <><a className="primary-button" href="/auth/login?persona=admin">Entrar como admin demo <span aria-hidden="true">→</span></a><a className="secondary-button" href="/auth/login?persona=buyer">Entrar como aluno demo</a><p>Contas demo: admin.demo@autonomia.site e user.demo@autonomia.site. Nenhuma senha é armazenada no código.</p></> : <a className="primary-button" href="/auth/login">Entrar no ambiente local <span aria-hidden="true">→</span></a>}</div></div>
        <div className="hero-visual" aria-hidden="true"><div className="visual-glow"/><div className="phone-card"><div className="phone-top"><span className="mini-mark">a</span><span>Visão geral</span><i/></div><p>Bom dia, creator.</p><h2>Seu negócio em movimento.</h2><div className="mini-metrics"><span><small>Produtos</small><strong>03</strong></span><span><small>Alunos</small><strong>128</strong></span></div><div className="mini-product"><i/><span><strong>Creator OS</strong><small>42% em andamento</small></span><b>→</b></div></div></div>
      </section>
      <section className="trust-strip" aria-label="Estado da prévia"><span>Interface mobile-first</span><span>Isolamento multitenant verificado em Local/CI</span><span>Sessão opaca no servidor</span></section>
    </main>
  );
}
