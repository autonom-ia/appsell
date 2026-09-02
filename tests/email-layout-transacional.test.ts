import { describe, expect, it } from "vitest";

import { origemPublica, renderizarEmailTransacional, TAGLINE } from "../lib/email/layout-transacional";
import { SITE } from "../lib/site";

// A moldura tem tres obrigacoes que a politica de e-mail impoe e que um
// template da #357 nao pode desfazer por descuido: identificar o remetente,
// dizer por que a pessoa recebe, e nunca virar vetor de injecao — tudo que
// entra e texto do banco ou do vendedor, e vai escapado.

const base = {
  assunto: "Seu acesso ao curso foi liberado",
  eyebrow: "Do lado de quem comprou",
  titulo: "A pessoa comprou. O acesso já é dela.",
  paragrafos: ["Primeiro parágrafo.", "Segundo parágrafo."],
  motivo: "comprou o curso Fotografia na loja Luz & Sombra"
} as const;

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

  it("escapa tudo que vem de fora: título, parágrafos, detalhes e rótulo do botão", () => {
    const { html } = renderizarEmailTransacional({
      ...base,
      titulo: "<script>alert(1)</script>",
      paragrafos: ['"aspas" & <b>negrito</b>'],
      detalhes: [{ rotulo: "<i>Curso</i>", valor: "Foto & Vídeo" }],
      acao: { rotulo: "<img src=x onerror=alert(1)>", url: "https://sell.autonomia.site/library" }
    });
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>negrito</b>");
    expect(html).not.toContain("<i>Curso</i>");
    // O texto escapado ainda contem "onerror=" como letras; o que nao pode
    // existir e a tag aberta de verdade.
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("Foto &amp; Vídeo");
  });

  it("só aceita botão com URL https absoluta", () => {
    for (const url of ["javascript:alert(1)", "http://sell.autonomia.site/x", "/library", "data:text/html,oi"]) {
      expect(() => renderizarEmailTransacional({ ...base, acao: { rotulo: "Abrir", url } })).toThrow(/https/);
    }
    const { html, text } = renderizarEmailTransacional({
      ...base,
      acao: { rotulo: "Abrir o curso", url: "https://sell.autonomia.site/library?x=1&y=2" }
    });
    expect(html).toContain('href="https://sell.autonomia.site/library?x=1&amp;y=2"');
    expect(text).toContain("Abrir o curso: https://sell.autonomia.site/library?x=1&y=2");
  });

  it("lê a origem pública com a mesma regra de lib/site.ts", () => {
    // A regra e repetida no layout de proposito (o script do Node nao consegue
    // importar ../site sem extensao). Se alguem mudar uma e nao a outra, o
    // e-mail passa a apontar a marca para outro dominio.
    expect(origemPublica()).toBe(SITE.origem);
  });

  it("aponta as imagens da marca para a origem pública, por URL absoluta", () => {
    const { html } = renderizarEmailTransacional(base);
    expect(html).toContain('src="https://sell.autonomia.site/lp/icone.png"');
    expect(html).toContain('src="https://sell.autonomia.site/lp/marca.png"');
    expect(html).not.toContain("data:image");
  });

  it("não carrega rastreador: nenhuma imagem além das duas da marca", () => {
    const { html } = renderizarEmailTransacional({
      ...base,
      detalhes: [{ rotulo: "Curso", valor: "Fotografia" }],
      acao: { rotulo: "Abrir", url: "https://sell.autonomia.site/library" }
    });
    expect(html.match(/<img\b/g)?.length).toBe(2);
  });

  it("gera a versão em texto com título, parágrafos, detalhes e botão, sem HTML", () => {
    const { text } = renderizarEmailTransacional({
      ...base,
      detalhes: [{ rotulo: "Curso", valor: "Fotografia" }],
      acao: { rotulo: "Abrir o curso", url: "https://sell.autonomia.site/library" }
    });
    expect(text).toContain("DO LADO DE QUEM COMPROU");
    expect(text).toContain("A pessoa comprou. O acesso já é dela.");
    expect(text).toContain("Curso: Fotografia");
    expect(text).not.toMatch(/<[a-z]+[\s>]/);
  });

  it("usa o primeiro parágrafo como preheader e o assunto como título da página", () => {
    const { html } = renderizarEmailTransacional(base);
    expect(html).toContain("<title>Seu acesso ao curso foi liberado</title>");
    expect(html.indexOf("Primeiro parágrafo.")).toBeLessThan(html.indexOf("<h1"));
  });
});
