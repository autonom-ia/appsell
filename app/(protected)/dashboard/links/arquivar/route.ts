import { NextRequest } from "next/server";

import {
  canonicalUuid,
  mappedError
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { archiveDomainCampaignLink } from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import { mutacaoValida, pedidoDoPainel, respostaJson } from "@/lib/tracking/painel.server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LIMITE_DO_FORMULARIO = 1_024;

// Arquiva um link (#563). O link arquivado continua na lista e nas vendas.
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
  const seller = form.get("seller");
  const pedido = await pedidoDoPainel(request, typeof seller === "string" ? seller : null);
  if (!pedido.ok) return pedido.response;
  const link = form.get("link");
  const linkId = canonicalUuid(typeof link === "string" ? link : undefined);
  if (!linkId) return respostaJson({ error: "invalid_campaign_link" }, 422);
  try {
    await archiveDomainCampaignLink(pedido.sessionId, pedido.session, pedido.sellerId, linkId);
    return respostaJson({ archived: true });
  } catch (error) {
    return mappedError(error);
  }
}
