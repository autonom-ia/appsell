export default async function Home({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return (
    <main className="landing">
      <nav className="landing-nav" aria-label="Navegação"><a className="landing-brand" href="#inicio"><span className="brand-mark" aria-hidden="true">a</span><strong>sell<span>.</span></strong></a><span className="demo-pill">DEMO LOCAL</span></nav>
      {error === "session_unavailable" ? <p className="landing-alert" role="alert"><strong>Sessão indisponível.</strong> O ambiente local não respondeu. Tente entrar novamente em alguns instantes.</p> : null}
      <section id="inicio" className="hero">
        <div className="hero-copy"><p className="eyebrow">Prévia da Autonom.ia Sell</p><h1>Seu conhecimento.<br/><span>Seu negócio.</span></h1><p className="hero-lead">Explore como será a experiência mobile para criar, vender e entregar produtos digitais. Persistência, cobrança e publicação ainda não estão conectadas.</p><div className="hero-actions"><a className="primary-button" href="/auth/login">Entrar no ambiente local <span aria-hidden="true">→</span></a><p>Identity real permanece bloqueada até certificação em staging.</p></div></div>
        <div className="hero-visual" aria-hidden="true"><div className="visual-glow"/><div className="phone-card"><div className="phone-top"><span className="mini-mark">a</span><span>Visão geral</span><i/></div><p>Bom dia, creator.</p><h2>Seu negócio em movimento.</h2><div className="mini-metrics"><span><small>Produtos</small><strong>03</strong></span><span><small>Alunos</small><strong>128</strong></span></div><div className="mini-product"><i/><span><strong>Creator OS</strong><small>42% em andamento</small></span><b>→</b></div></div></div>
      </section>
      <section className="trust-strip" aria-label="Estado da prévia"><span>Interface mobile-first</span><span>Isolamento multitenant em implementação</span><span>Sessão opaca no servidor</span></section>
    </main>
  );
}
