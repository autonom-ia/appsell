import { SITE } from "@/lib/site";

/**
 * Dados estruturados da pagina publica. Nao renderiza nada visivel: o buscador
 * le, a pessoa nao ve. Cada campo aqui existe de fato na pagina, incluindo as
 * perguntas, que sao as mesmas da secao de duvidas. Marcacao sem conteudo
 * correspondente e o caminho curto para penalidade.
 */
export function LpDados({ perguntas }: { perguntas: ReadonlyArray<readonly string[]> }) {
  const grafo = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${SITE.origem}/#organizacao`,
        name: SITE.organizacao,
        url: "https://autonomia.site/",
        logo: `${SITE.origem}/lp/icone.png`,
        sameAs: ["https://www.linkedin.com/company/autonom-ia-solutions/"]
      },
      {
        "@type": "WebSite",
        "@id": `${SITE.origem}/#site`,
        url: `${SITE.origem}/`,
        name: SITE.nome,
        inLanguage: "pt-BR",
        publisher: { "@id": `${SITE.origem}/#organizacao` }
      },
      {
        "@type": "SoftwareApplication",
        "@id": `${SITE.origem}/#produto`,
        name: SITE.nome,
        applicationCategory: "BusinessApplication",
        operatingSystem: "Web",
        url: `${SITE.origem}/`,
        inLanguage: "pt-BR",
        publisher: { "@id": `${SITE.origem}/#organizacao` },
        description:
          "Plataforma para vender cursos em uma loja com a marca de quem vende. O aluno assiste video e PDF pelo navegador do celular, sem instalar aplicativo.",
        offers: {
          "@type": "Offer",
          "@id": `${SITE.origem}/#oferta`,
          priceCurrency: "BRL",
          // Nao ha mensalidade: o custo e por venda, entao o preco de entrada e
          // zero e a comissao vai descrita, que e como o schema representa isso.
          price: 0,
          description: `Sem mensalidade. ${SITE.taxaPercentual.toFixed(2).replace(".", ",")}% mais R$ ${SITE.taxaFixa.toFixed(2).replace(".", ",")} por venda.`,
          availability: "https://schema.org/InStock"
        }
      },
      {
        "@type": "FAQPage",
        "@id": `${SITE.origem}/#duvidas`,
        mainEntity: perguntas.map(([pergunta, resposta]) => ({
          "@type": "Question",
          name: pergunta,
          acceptedAnswer: { "@type": "Answer", text: resposta }
        }))
      }
    ]
  };

  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(grafo) }} />;
}
