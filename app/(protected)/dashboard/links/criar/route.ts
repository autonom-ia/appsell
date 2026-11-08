import { NextRequest } from "next/server";

import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { createDomainCampaignLink } from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import { mutacaoValida, pedidoDoPainel, respostaJson } from "@/lib/tracking/painel.server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LIMITE_DO_FORMULARIO = 4_096;

function campo(form: FormData, nome: string): string | null {
  const valor = form.get(nome);
  if (typeof valor !== "string") return null;
  const texto = valor.trim();
  return texto.length === 0 ? null : texto;
}

// Cria um link UTM da loja (#563). A API e o banco validam de novo.
export async function POST(request: NextRequest) {
  let form: FormData | null;
  try {
    form = await readLimitedForm(request, LIMITE_DO_FORMULARIO);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) return respostaJson({ error: "payload_too_large" }, 413);
    form = null;
  }
  if (!form || !mutacaoValida(request, form.get("_csrf"))) {
    return respostaJson({ error: "forbidden" }, 403);
  }
  const pedido = await pedidoDoPainel(request, campo(form, "seller"));
  if (!pedido.ok) return pedido.response;
  const nome = campo(form, "name");
  const fonte = campo(form, "utm_source");
  if (!nome || !fonte) return respostaJson({ error: "invalid_campaign_link" }, 422);
  try {
    const link = await createDomainCampaignLink(pedido.sessionId, pedido.session, pedido.sellerId, {
      name: nome,
      destinationPageSlug: campo(form, "destination_page_slug"),
      utmSource: fonte,
      utmMedium: campo(form, "utm_medium"),
      utmCampaign: campo(form, "utm_campaign"),
      utmContent: campo(form, "utm_content"),
      utmTerm: campo(form, "utm_term")
    });
    return respostaJson(link, 201);
  } catch (error) {
    return mappedError(error);
  }
}
