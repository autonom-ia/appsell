import type { MetadataRoute } from "next";

import { SITE } from "@/lib/site";

// Fora a pagina publica e as lojas, tudo aqui e area logada, fluxo de
// autenticacao ou link de convite de uso unico. Nada disso deve entrar em
// indice de busca, e o link de convite nem deveria sair da mao de quem recebeu.
export default function robots(): MetadataRoute.Robots {
  // Area logada, fluxo de autenticacao e link de convite de uso unico. O
  // convite nem deveria sair da mao de quem recebeu.
  const privado = ["/api/", "/auth/", "/checkout", "/dashboard", "/library", "/payouts", "/products", "/platform/", "/c/"];

  // Os robos de IA se dividem em dois usos, e a escolha e diferente para cada
  // um. Os de busca leem a pagina na hora de responder alguem e podem mandar
  // visita de volta. Os de treinamento levam o texto para dentro do modelo sem
  // citar a origem. Enquanto o objetivo e ser descoberto, os dois estao
  // liberados; se um dia o texto proprio virar ativo a proteger, basta trocar
  // allow por disallow no segundo grupo.
  const buscaPorIA = ["OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User", "Claude-User", "Claude-SearchBot"];
  const treinoDeIA = ["GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "CCBot", "meta-externalagent"];

  return {
    rules: [
      { userAgent: "*", allow: "/", disallow: privado },
      { userAgent: buscaPorIA, allow: "/", disallow: privado },
      { userAgent: treinoDeIA, allow: "/", disallow: privado }
    ],
    sitemap: `${SITE.origem}/sitemap.xml`,
    host: SITE.origem
  };
}
