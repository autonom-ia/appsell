import { CAMPOS_UTM, PARAMETRO_DO_LINK } from "./attribution";

export interface LinkDaLoja {
  id: string;
  destination_page_slug: string | null;
  utm_source: string;
  utm_medium: string | null;
  utm_campaign: string | null;
  utm_content: string | null;
  utm_term: string | null;
}

// URL pública do link: a vitrine (/s/<loja>) ou a página do produto
// (/s/<loja>/p/<produto>), com os campos UTM e o id do link em `sl`.
export function urlDoLink(origem: string, slugDaLoja: string, link: LinkDaLoja): string {
  const pagina = link.destination_page_slug === null
    ? ""
    : `/p/${encodeURIComponent(link.destination_page_slug)}`;
  const url = new URL(`/s/${encodeURIComponent(slugDaLoja)}${pagina}`, origem);
  for (const campo of CAMPOS_UTM) {
    const valor = link[campo];
    if (valor !== null && valor !== undefined) url.searchParams.set(campo, valor);
  }
  url.searchParams.set(PARAMETRO_DO_LINK, link.id);
  return url.toString();
}
