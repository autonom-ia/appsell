import { afterEach, describe, expect, it } from "vitest";

import {
  type EmailTransacional,
  origemPublica,
  renderizarEmailTransacional,
  TAGLINE
} from "../lib/email/layout-transacional";
import { SITE } from "../lib/site";

// A moldura tem tres obrigacoes que a politica de e-mail impoe e que um
// template da #357 nao pode desfazer por descuido: identificar o remetente,
// dizer por que a pessoa recebe, e nunca virar vetor de injecao — tudo que
// entra e texto do banco ou do vendedor, e vai escapado.
//
// Caracteres de controle sao escritos como \uNNNN de proposito: um byte de
// controle literal no fonte torna o arquivo binario e irrevisavel.

const ORIGEM = "https://sell.autonomia.site";

const base = {
  assunto: "Seu acesso ao curso foi liberado",
  eyebrow: "Do lado de quem comprou",
  titulo: "A pessoa comprou. O acesso já é dela.",
  paragrafos: ["Primeiro parágrafo.", "Segundo parágrafo."],
  motivo: "comprou o curso Fotografia na loja Luz & Sombra"
} as const;

const HOSTIL = `&<>"' fim`;
const HOSTIL_ESCAPADO = "&amp;&lt;&gt;&quot;&#39; fim";

// Todo recurso remoto que o HTML pode referenciar. Qualquer outro e
// rastreador ou engano.
function recursosRemotos(html: string): string[] {
  const achados = new Set<string>();
  const atributo =
    /\b(?:src|href|action|background|poster|data|srcset|imagesrcset|ping)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))|url\(\s*["']?([^)"']+)["']?\s*\)|@import\s+(?:url\()?["']?([^"')\s;]+)/gi;
  for (const m of html.matchAll(atributo)) {
    achados.add((m[1] ?? m[2] ?? m[3] ?? m[4] ?? m[5]).replace(/&amp;/g, "&"));
  }
  return [...achados];
}

afterEach(() => {
  delete process.env.NEXT_PUBLIC_SITE_ORIGIN;
});

describe("renderizarEmailTransacional", () => {
  it("carrega o rodapé obrigatório da política em HTML e em texto", () => {
    const { html, text } = renderizarEmailTransacional(base);
    for (const saida of [html, text]) {
      expect(saida).toContain("Autonom.ia Tecnologia Ltda (CNPJ 59.303.298/0001-51)");
      expect(saida).toContain("não depende de inscrição e não tem descadastro");
      expect(saida).toContain("contato@autonomia.site");
      expect(saida).toContain(TAGLINE);
    }
    expect(html).toContain("você o recebe porque comprou o curso Fotografia na loja Luz &amp; Sombra");
    expect(text).toContain("você o recebe porque comprou o curso Fotografia na loja Luz & Sombra");
  });

  it("recusa e-mail sem motivo, porque o rodapé ficaria mentindo", () => {
    expect(() => renderizarEmailTransacional({ ...base, motivo: "   " })).toThrow(/motivo/);
  });

  it("renderiza o caso mínimo: sem eyebrow, sem detalhes, sem botão", () => {
    const { html, text } = renderizarEmailTransacional({
      assunto: "Assunto",
      titulo: "Título",
      paragrafos: ["Só um parágrafo."],
      motivo: "tem conta no Autonom.ia Sell"
    });
    expect(html).toContain("<h1");
    expect(html).toContain("Só um parágrafo.");
    expect(html).not.toContain("text-transform:uppercase;color:#c9420d");
    expect(html).not.toContain("border-collapse"); // sem tabela de detalhes
    expect(html).not.toContain("&rarr;"); // sem botão
    expect(recursosRemotos(html).sort()).toEqual(
      [`${ORIGEM}/lp/icone.png`, `${ORIGEM}/lp/marca.png`, `${ORIGEM}/privacidade`, "mailto:contato@autonomia.site"].sort()
    );
    expect(text).not.toContain("undefined");
    expect(text).toMatch(/^AUTONOM\.IA SELL\n\nTítulo\n/); // sem linha vazia do eyebrow
  });

  it("escapa todo campo que vem de fora, inclusive os que entram em atributo", () => {
    const { html } = renderizarEmailTransacional({
      assunto: HOSTIL,
      eyebrow: HOSTIL,
      titulo: `<script>alert(1)</script> ${HOSTIL}`,
      paragrafos: [`"aspas" & <b>negrito</b> ${HOSTIL}`],
      detalhes: [{ rotulo: `<i>Curso</i> ${HOSTIL}`, valor: `Foto & Vídeo ${HOSTIL}` }],
      acao: { rotulo: `<img src=x onerror=alert(1)> ${HOSTIL}`, url: `${ORIGEM}/library?a=1&b="2"` },
      motivo: HOSTIL
    });
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>negrito</b>");
    expect(html).not.toContain("<i>Curso</i>");
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    // Sete campos hostis; o paragrafo aparece duas vezes (corpo e preheader) e
    // o assunto uma (<title>): nove ocorrencias escapadas, zero cruas.
    expect(html.match(new RegExp(HOSTIL_ESCAPADO, "g"))?.length).toBe(9);
    expect(html).not.toContain(HOSTIL);
    // A aspa na query do botao nao pode fechar o atributo href.
    expect(html).toContain(`href="${ORIGEM}/library?a=1&amp;b=%222%22"`);
  });

  it("recusa quebra de linha e caractere de controle em qualquer campo de uma linha", () => {
    const casos: Array<Partial<EmailTransacional>> = [
      { motivo: "linha um\nlinha dois" },
      { motivo: "com retorno\r" },
      { assunto: "assunto\u0000nulo" },
      { titulo: "título\u001bescape" },
      { eyebrow: "eye\u007fbrow" },
      { eyebrow: "nel\u0085aqui" },
      { titulo: "separador\u2028de linha" },
      { titulo: "paragrafo\u2029unicode" },
      { motivo: "bidi\u202einvertido" },
      { motivo: "isolado\u2066bidi\u2069" },
      { motivo: "marca\u200eesquerda" },
      { motivo: "marca\u200fdireita" },
      { motivo: "arabe\u061cmark" },
      { paragrafos: ["ok", "quebra\ndentro"] },
      { acao: { rotulo: "abrir\n", url: `${ORIGEM}/x` } }
    ];
    for (const caso of casos) {
      expect(() => renderizarEmailTransacional({ ...base, ...caso })).toThrow(/controle/);
    }
    expect(() =>
      renderizarEmailTransacional({ ...base, detalhes: [{ rotulo: "Curso", valor: "a\tb" }] })
    ).toThrow(/controle/);
  });

  it("só aceita botão https na origem pública, sem credenciais", () => {
    const recusadas = [
      "javascript:alert(1)",
      "http://sell.autonomia.site/x",
      "/library",
      "data:text/html,oi",
      "https://evil.com/",
      "https://sell.autonomia.site@evil.com/",
      "https://u:p@sell.autonomia.site/library",
      "https://sell.autonomia.site.evil.com/",
      "https://sell.autonomia.site:8443/"
    ];
    for (const url of recusadas) {
      expect(() => renderizarEmailTransacional({ ...base, acao: { rotulo: "Abrir", url } })).toThrow(/https/);
    }
    const { html, text } = renderizarEmailTransacional({
      ...base,
      acao: { rotulo: "Abrir o curso", url: `${ORIGEM}/library?x=1&y=2` }
    });
    expect(html).toContain(`href="${ORIGEM}/library?x=1&amp;y=2"`);
    expect(text).toContain(`Abrir o curso: ${ORIGEM}/library?x=1&y=2`);
  });

  it("recusa botão cujo parâmetro denuncia segredo de sessão ou de conta", () => {
    const recusadas = [
      `${ORIGEM}/library?session_token=abc`,
      `${ORIGEM}/library?token=abc`,
      `${ORIGEM}/login?password=abc`,
      `${ORIGEM}/x?Access_Token=abc`,
      `${ORIGEM}/invite#access_token=abc`,
      `${ORIGEM}/invite#sid=abc&ok=1`,
      `${ORIGEM}/x?jwt=abc`,
      `${ORIGEM}/x?credential=abc`,
      `${ORIGEM}/x?accessKey=abc`,
      `${ORIGEM}/x?refreshToken=abc`,
      `${ORIGEM}/x?x-auth=abc`,
      `${ORIGEM}/x?senha_nova=abc`,
      `${ORIGEM}/x?sessiontoken=abc`
    ];
    for (const url of recusadas) {
      expect(() => renderizarEmailTransacional({ ...base, acao: { rotulo: "Abrir", url } })).toThrow(/proibido/);
    }
    // O erro nao pode repetir a chave: se o segredo vier como nome, iria
    // parar no log de quem capturar a excecao.
    let mensagem = "";
    try {
      renderizarEmailTransacional({ ...base, acao: { rotulo: "Abrir", url: `${ORIGEM}/x?Bearer%20eyJSEGREDO=1` } });
    } catch (erro) {
      mensagem = erro instanceof Error ? erro.message : String(erro);
    }
    expect(mensagem).toMatch(/proibido/);
    expect(mensagem).not.toContain("SEGREDO");
    expect(mensagem).not.toContain("Bearer");
    // Nome benigno que contem pedaco de palavra proibida passa; e link de uso
    // unico com nome proprio passa — inclusive o "#t=" do convite na SPEC.
    const permitidas = [
      `${ORIGEM}/invite#t=abc`,
      `${ORIGEM}/invite#convite=abc`,
      `${ORIGEM}/redefinir?codigo=abc`,
      `${ORIGEM}/c/loja?utm=x`,
      `${ORIGEM}/avaliacao?assessment_id=1`,
      `${ORIGEM}/blog?author=ana`,
      `${ORIGEM}/x?passo=2`
    ];
    for (const url of permitidas) {
      expect(() => renderizarEmailTransacional({ ...base, acao: { rotulo: "Abrir", url } })).not.toThrow();
    }
  });

  it("lê a origem pública com a mesma regra de lib/site.ts", () => {
    // A regra e repetida no layout de proposito (o script do Node nao consegue
    // importar ../site sem extensao). Se alguem mudar uma e nao a outra, o
    // e-mail passa a apontar a marca para outro dominio.
    expect(origemPublica()).toBe(new URL(SITE.origem).origin);
  });

  it("normaliza a origem pública e recusa origem que quebraria o atributo", () => {
    process.env.NEXT_PUBLIC_SITE_ORIGIN = "https://preview.example.test/";
    expect(origemPublica()).toBe("https://preview.example.test");
    expect(renderizarEmailTransacional(base).html).toContain('src="https://preview.example.test/lp/icone.png"');
    expect(renderizarEmailTransacional(base).html).not.toContain("//lp/");

    for (const ruim of ["http://x.test", "https://u:p@x.test", "sem-esquema", ""]) {
      process.env.NEXT_PUBLIC_SITE_ORIGIN = ruim;
      expect(() => renderizarEmailTransacional(base)).toThrow(/NEXT_PUBLIC_SITE_ORIGIN|https/);
    }

    // Caminho com aspa e atributo e uma URL valida; so a origem e usada, e o
    // resto nao chega ao HTML.
    process.env.NEXT_PUBLIC_SITE_ORIGIN = 'https://x.test/"onload="alert(1)';
    const { html } = renderizarEmailTransacional(base);
    expect(html).not.toContain("onload");
    expect(html).toContain('src="https://x.test/lp/icone.png"');
  });

  it("não carrega rastreador: todo recurso remoto está numa lista fechada", () => {
    const { html } = renderizarEmailTransacional({
      ...base,
      detalhes: [{ rotulo: "Curso", valor: "Fotografia" }],
      acao: { rotulo: "Abrir", url: `${ORIGEM}/library` }
    });
    expect(recursosRemotos(html).sort()).toEqual(
      [
        `${ORIGEM}/lp/icone.png`,
        `${ORIGEM}/lp/marca.png`,
        `${ORIGEM}/library`,
        `${ORIGEM}/privacidade`,
        "mailto:contato@autonomia.site"
      ].sort()
    );
    expect(html).not.toContain("data:image");
    expect(html).not.toMatch(/<iframe|background-image|\?utm_|\?t=/);
  });

  it("gera a versão em texto com título, parágrafos, detalhes e botão, sem HTML", () => {
    const { text } = renderizarEmailTransacional({
      ...base,
      detalhes: [{ rotulo: "Curso", valor: "Fotografia" }],
      acao: { rotulo: "Abrir o curso", url: `${ORIGEM}/library` }
    });
    expect(text).toContain("DO LADO DE QUEM COMPROU");
    expect(text).toContain("A pessoa comprou. O acesso já é dela.");
    expect(text).toContain("Primeiro parágrafo.");
    expect(text).toContain("Curso: Fotografia");
    expect(text).not.toMatch(/<[a-z]+[\s>]/);
    expect(text).not.toContain("&amp;");
  });

  it("usa o primeiro parágrafo como preheader escondido e o assunto como título da página", () => {
    const { html } = renderizarEmailTransacional(base);
    expect(html).toContain("<title>Seu acesso ao curso foi liberado</title>");
    const preheader = html.indexOf("Primeiro parágrafo.");
    expect(preheader).toBeLessThan(html.indexOf("<h1"));
    expect(html.slice(preheader - 200, preheader)).toMatch(/display:none;.*mso-hide:all;/);
  });
});
