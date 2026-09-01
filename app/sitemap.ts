import type { MetadataRoute } from "next";

import { SITE } from "@/lib/site";

// So o que e publico, estavel e tem conteudo proprio. As lojas dos vendedores
// ficam de fora ate existir uma listagem no backend: enderecos chutados no
// sitemap viram 404 no relatorio do Search Console.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE.origem}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${SITE.origem}/termos`, changeFrequency: "yearly", priority: 0.3 },
    { url: `${SITE.origem}/privacidade`, changeFrequency: "yearly", priority: 0.3 },
    { url: `${SITE.origem}/cookies`, changeFrequency: "yearly", priority: 0.3 }
  ];
}
