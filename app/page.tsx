import type { Metadata } from "next";
import { demoModeEnabled } from "@/lib/demo-config";
import { LpContato } from "./lp-contato";
import { LpMotion } from "./lp-motion";
import { LpPreco } from "./lp-preco";
import "./lp.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Autonom.ia Sell, sua loja de cursos com a sua marca",
  description:
    "Venda seu conhecimento numa loja com a sua marca. Sem mensalidade: 6,99% mais R$ 1,99 por venda. O aluno assiste pelo navegador do celular.",
  alternates: { canonical: "https://sell.autonomia.site/" },
  openGraph: {
    type: "website",
    siteName: "Autonom.ia Sell",
    title: "Autonom.ia Sell, sua loja de cursos com a sua marca",
    description: "Sem mensalidade: 6,99% mais R$ 1,99 por venda. O aluno assiste pelo navegador do celular.",
    url: "https://sell.autonomia.site/",
    locale: "pt_BR"
  }
};

const NOS = [
  { titulo: "Sua loja no ar", texto: "Um endereço com o seu nome, pronto para mandar no story e na mensagem. A vitrine já nasce sua." },
  { titulo: "Seu curso montado", texto: "Vídeo, PDF e material de apoio organizados em aulas, na ordem em que você ensina." },
  { titulo: "Publicação de uma vez", texto: "Um clique põe o curso inteiro no ar, e o aluno nunca encontra aula faltando." },
  { titulo: "O aluno já está dentro", texto: "Ele paga, o acesso entra no nome dele e a aula abre no celular, no mesmo minuto.", humano: true }
];

const LINHAS = [
  ["Sua marca na frente", "O nome na vitrine, na área do aluno e na aula é o seu. A Autonom.ia assina como a base por trás."],
  ["Cada aluno com o que é dele", "Quem comprou abre a própria biblioteca e vê o progresso de cada curso, sem lista de acesso na sua mão."],
  ["Vídeo e PDF na mesma aula", "O material abre na própria página. O aluno estuda no celular sem baixar arquivo."],
  ["Curso inteiro no ar", "A publicação vale para o produto todo, então ninguém abre uma aula e encontra buraco."],
  ["Cortesia ou venda", "Libere de graça para uma lista ou cobre pelo acesso, sem montar o curso de novo."],
  ["O recebimento é seu", "A conta que recebe pelas vendas é sua, conectada uma vez e verificada dentro da plataforma."]
];

const PERGUNTAS = [
  ["Como o aluno entra na aula?", "Ele abre no navegador do celular, sem instalar aplicativo. A interface foi desenhada para a tela pequena antes da grande."],
  ["Posso usar vídeo e PDF no mesmo curso?", "Pode, e na mesma aula. O aluno lê o documento dentro da plataforma e assiste ao vídeo ali do lado."],
  ["A loja fica com a minha marca?", "Fica. O nome que o aluno vê na vitrine e na área dele é o da sua loja. A Autonom.ia assina como a plataforma por trás."],
  ["E se eu já vendo em outro lugar?", "Você aponta o seu público para o link da sua loja e passa a controlar o acesso por pessoa. Na conversa a gente vê como fica a migração."],
  ["O conteúdo fica seguro?", "Só quem comprou consegue abrir. Passar o link adiante não dá acesso a ninguém, porque o que vale é a pessoa que entrou, não o endereço."]
];

const SETA = (
  <svg className="i" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 12h15M13 6l6 6-6 6" />
  </svg>
);

export default async function Home({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  const demo = demoModeEnabled();

  return (
    <div className="lp">
      <a className="pular" href="#conteudo">Pular para o conteúdo</a>

      <header className="top">
        <a className="mark" href="#topo" aria-label="Autonom.ia Sell, início">
          <span className="ico" />
          <span><b>Autonom.ia</b><small>Sell</small></span>
        </a>
        <nav className="topnav" aria-label="Principal">
          <a href="#funciona">Como funciona</a>
          <a href="#loja">O produto</a>
          <a href="#preco">Preço</a>
          <a href="#contato">Falar com a equipe</a>
          <a href="/auth/login">Entrar</a>
          <a href="/auth/signup" className="cta-topo">Vender agora</a>
        </nav>
        <span className="topo-mob">
          <a href="/auth/login">Entrar</a>
          <a className="cta-mob" href="/auth/signup">Vender</a>
        </span>
      </header>

      <main id="conteudo">
        {error === "session_unavailable" ? (
          <p className="lp-alerta" role="alert">
            <strong>Sessão indisponível.</strong> O login não respondeu. Tente entrar novamente em alguns instantes.
          </p>
        ) : null}

        {demo ? (
          <p className="lp-demo">
            Ambiente de demonstração:{" "}
            <a href="/auth/login?persona=admin">entrar como admin</a> ou{" "}
            <a href="/auth/login?persona=buyer">entrar como aluno</a>.
          </p>
        ) : null}

        <section className="abertura" id="topo">
          <div className="pin">
            <div className="foto" id="foto" aria-hidden="true">
              {/* O video e um bonus: nasce ausente e so e criado pelo cliente
                  quando a tela, a conexao e a preferencia de movimento
                  permitem. Sem ele a foto continua sendo o fundo. */}
            </div>

            <div className="in palco-grid">
              <div className="atos">
                <article className="ato" data-ato="0">
                  <span className="eb">Loja de cursos com a sua marca</span>
                  <h1>
                    <span className="linha"><span>A pessoa comprou.</span></span>{" "}
                    <span className="linha"><span><b>O acesso já é dela.</b></span></span>
                  </h1>
                  <p className="sub">
                    A compra libera o curso no nome de quem pagou. Você não manda link nem passa senha, e o aluno assiste vídeo e PDF pelo navegador do celular.
                  </p>
                  <p className="preco-linha">Sem mensalidade: 6,99% + R$ 1,99 por venda.</p>
                  <div className="actions">
                    <a className="cta" href="/auth/signup">Vender agora {SETA}</a>
                    <a className="cta-2" href="#contato">Falar com a equipe</a>
                  </div>
                  <p className="nota">Você cria a conta agora mesmo. Se preferir, a gente monta a loja junto com você.</p>
                </article>

                <article className="ato" data-ato="1" aria-hidden="true">
                  <span className="eb">Do lado de quem comprou</span>
                  <p className="act-title">
                    <span className="linha"><span>Abre no celular</span></span>{" "}
                    <span className="linha"><span><b>e a aula já começa.</b></span></span>
                  </p>
                  <p className="sub">
                    Roda no navegador, sem instalar aplicativo. Cada aluno vê a própria biblioteca e de onde parou em cada curso.
                  </p>
                </article>

                <article className="ato" data-ato="2" aria-hidden="true">
                  <span className="eb">Quanto custa</span>
                  <p className="act-title">
                    <span className="linha"><span>Sem mensalidade.</span></span>{" "}
                    <span className="linha"><span><b>6,99% + R$ 1,99 por venda.</b></span></span>
                  </p>
                  <p className="sub">
                    Você só paga quando alguém compra. Mês parado não gera cobrança, e cortesia não é venda.
                  </p>
                </article>
              </div>
            </div>

            <div className="palco" aria-hidden="true">
              <div className="fone">
                <div className="corpo" id="corpo-hero">
                  <div className="vidro">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src="/lp/produtos.webp" alt="" width={1000} height={1833} fetchPriority="high" />
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src="/lp/painel.webp" id="t-painel" alt="" width={1000} height={1833} />
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src="/lp/recebimentos.webp" id="t-recebe" alt="" width={1000} height={1833} />
                    <span className="reflexo" />
                  </div>
                  <span className="contorno" />
                  <span className="sombra-chao" />
                  <span className="peca" id="peca-painel" style={{ top: "52%" }}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src="/lp/peca-turma.webp" alt="" width={510} height={174} loading="lazy" />
                  </span>
                </div>
              </div>
            </div>

            <div className="progresso" aria-hidden="true">
              <i className="on">A venda</i><i>O aluno</i><i>O preço</i>
            </div>
          </div>
        </section>

        <div className="so-leitor">
          <h2>Abre no celular e a aula já começa</h2>
          <p>Roda no navegador, sem instalar aplicativo. Cada aluno vê a própria biblioteca e de onde parou em cada curso.</p>
          <h2>Sem mensalidade. 6,99% mais R$ 1,99 por venda.</h2>
          <p>Você só paga quando alguém compra. Mês parado não gera cobrança, e cortesia não é venda.</p>
        </div>

        <section className="tese">
          <div className="in">
            <h2>Quem compra de você <span className="qb" /><b>compra de você.</b></h2>
            <p>
              Na sua loja o seu nome está na vitrine, na área do aluno e dentro da aula. A lista de quem comprou é sua, o próximo lançamento você anuncia no seu endereço, e a relação continua no seu nome. Enquanto isso o acesso se resolve sozinho: a compra libera, o conteúdo mora num lugar só, e o aluno entra pelo celular quando quiser.
            </p>
          </div>
        </section>

        <section className="trilho-sec" id="funciona">
          <div className="in">
            <div className="cabeca">
              <span className="eb escuro">Do arquivo à aula</span>
              <h2>Do arquivo no seu computador <span className="qb" />à aula <b>no celular do aluno</b>.</h2>
            </div>
            <div className="trilho">
              <div className="via" aria-hidden="true"><i id="via-luz" /></div>
              <p className="eixo" aria-hidden="true"><span>O que você faz</span><span>O que acontece sozinho</span></p>
              <div className="nos">
                {NOS.map((no) => (
                  <div className={no.humano ? "no humano" : "no"} key={no.titulo}>
                    <h3>{no.titulo}</h3>
                    <p>{no.texto}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="loja" id="loja">
          <div className="in">
            <span className="eb">Por dentro</span>
            <h2>A loja é sua. <span className="qb" /><b>A base é nossa.</b></h2>
            <p className="lead">É isto que o seu aluno vê quando entra.</p>
            <div className="loja-grid">
              <div className="palco-loja" aria-hidden="true">
                <div className="fone">
                  <div className="corpo" id="corpo-loja">
                    <div className="vidro">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src="/lp/aula.webp" alt="" width={1000} height={1833} loading="lazy" />
                      <span className="reflexo" />
                    </div>
                    <span className="contorno" />
                    <span className="sombra-chao" />
                    <span className="peca" id="peca-aula" style={{ top: "48%" }}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src="/lp/peca-biblioteca.webp" alt="" width={1061} height={409} loading="lazy" />
                    </span>
                  </div>
                </div>
              </div>
              <div className="linhas">
                {LINHAS.map(([titulo, texto]) => (
                  <div key={titulo}>
                    <h3>{titulo}</h3>
                    <p>{texto}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="preco" id="preco">
          <div className="in"><h2 className="eb">Sem mensalidade</h2></div>
          <div className="regua" />
          <div className="in">
            <div className="numero-grid">
              <p className="numero">
                <span className="l">6,99<em>%</em></span>
                <span className="l"><em>+</em> <em>R$</em> 1,99</span>
                <span className="rot">por venda</span>
              </p>
              <div className="garantias">
                <p><b>Sem taxa para começar</b> e sem cobrança mensal.</p>
                <p>Cortesia não é venda, então não é cobrada.</p>
                <p>Mês sem venda, fatura zero.</p>
              </div>
            </div>
          </div>
          <div className="regua" />
          <div className="in">
            <LpPreco />
            <div className="actions">
              <a className="cta-vender" href="/auth/signup">Vender agora {SETA}</a>
              <a className="cta-2 escuro" href="#contato">Falar com a equipe</a>
            </div>
            <p className="conta-nota">Criar a conta não custa nada. A taxa só existe quando alguém compra de você.</p>
          </div>
        </section>

        <section className="perguntas">
          <div className="in">
            <h2>O que costumam perguntar antes.</h2>
            <div className="qa">
              {PERGUNTAS.map(([pergunta, resposta], i) => (
                <details key={pergunta} open={i === 0}>
                  <summary>{pergunta}</summary>
                  <p>{resposta}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="contato" id="contato">
          <div className="in">
            <span className="eb">Fale com a equipe</span>
            <h2>Conte o que você vende e <span className="qb" />a gente <b>monta a sua loja com você</b>.</h2>
            <p className="lead">
              Quem responde é quem constrói a plataforma. Na primeira conversa a gente olha o seu catálogo, confirma a conta que vai receber e você sai sabendo quanto entra em cada venda.
            </p>
            <LpContato />
          </div>
        </section>
      </main>

      <footer className="rodape">
        <div className="in">
          <div className="rodape-grid">
            <div>
              <span className="wm" role="img" aria-label="Autonom.ia" />
              <p className="tag">Autonom.ia Sell é a plataforma da Autonom.ia para criar, vender e entregar produtos digitais.</p>
              <div className="social">
                <a href="https://www.instagram.com/autonom.ia_/" aria-label="Instagram da Autonom.ia" rel="noopener">
                  <svg className="i" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.16c3.2 0 3.58.01 4.85.07 3.25.15 4.77 1.69 4.92 4.92.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.15 3.23-1.66 4.77-4.92 4.92-1.27.06-1.64.07-4.85.07s-3.58-.01-4.85-.07c-3.26-.15-4.77-1.7-4.92-4.92C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85c.15-3.23 1.66-4.77 4.92-4.92C8.42 2.17 8.8 2.16 12 2.16zm0 3.68a6.16 6.16 0 1 0 0 12.32 6.16 6.16 0 0 0 0-12.32zm0 10.16a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm6.41-11.85a1.44 1.44 0 1 0 0 2.88 1.44 1.44 0 0 0 0-2.88z" /></svg>
                </a>
                <a href="https://www.linkedin.com/company/autonom-ia-solutions/" aria-label="LinkedIn da Autonom.ia" rel="noopener">
                  <svg className="i" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M20.45 20.45h-3.55v-5.57c0-1.33-.03-3.04-1.86-3.04-1.85 0-2.13 1.45-2.13 2.94v5.67H9.35V9h3.42v1.56h.04c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46zM5.34 7.43a2.07 2.07 0 1 1 0-4.13 2.07 2.07 0 0 1 0 4.13zM7.12 20.45H3.55V9h3.57zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z" /></svg>
                </a>
              </div>
            </div>
            <nav aria-label="Produto">
              <h3>Produto</h3>
              <ul>
                <li><a href="#funciona">Como funciona</a></li>
                <li><a href="#loja">O produto</a></li>
                <li><a href="#preco">Quanto custa</a></li>
                <li><a href="/auth/login">Entrar</a></li>
              </ul>
            </nav>
            <nav aria-label="Legal">
              <h3>Legal</h3>
              <ul>
                <li><a href="/privacidade">Política de Privacidade</a></li>
                <li><a href="/termos">Termos de Uso</a></li>
                <li><a href="/cookies">Política de Cookies</a></li>
              </ul>
            </nav>
          </div>
          <div className="base">
            <span>© 2026 Autonom.ia Tecnologia Ltda. Todos os direitos reservados.</span>
          </div>
        </div>
      </footer>

      <LpMotion />
    </div>
  );
}
