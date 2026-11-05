import { NextRequest, NextResponse } from "next/server";

import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import { lerEscolha } from "@/lib/tracking/consent";
import { limiteDoConsentimento } from "@/lib/tracking/consent-limit";
import { atribuicaoLiberada } from "@/lib/tracking/pilot";
import { uuidCanonico } from "@/lib/tracking/attribution";
import { chamarRastreio } from "@/lib/tracking/servico.server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LIMITE_DO_CORPO = 512;
const CABECALHOS = { "cache-control": "no-store" };

function ipDe(request: NextRequest): string {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip") || "desconhecido";
}

function vazio(status: number) {
  return new NextResponse(null, { status, headers: CABECALHOS });
}

// Conta o aceite ou a recusa do banner de cookies (#563), sem dado pessoal:
// loja, versão do texto e a escolha. O banner chama da própria página da loja.
// Responde 204 para qualquer pedido aceito; loja fora do piloto não conta.
export async function POST(request: NextRequest) {
  if (request.headers.get("sec-fetch-site") !== "same-origin") return vazio(403);
  if (!limiteDoConsentimento.dentro(ipDe(request))) return vazio(429);
  let form: FormData | null;
  try {
    form = await readLimitedForm(request, LIMITE_DO_CORPO);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) return vazio(413);
    form = null;
  }
  const sellerBruto = form?.get("seller");
  const escolhaBruta = form?.get("choice");
  const seller = uuidCanonico(typeof sellerBruto === "string" ? sellerBruto : null);
  const escolha = lerEscolha(typeof escolhaBruta === "string" ? escolhaBruta : undefined);
  if (!seller || !escolha) return vazio(422);
  if (!atribuicaoLiberada(seller)) return vazio(204);
  try {
    await chamarRastreio("/v1/public/tracking/cookie-consent", {
      seller_organization_id: seller,
      text_version: escolha.versao,
      accepted: escolha.escolha === "accepted"
    });
  } catch {
    return vazio(503);
  }
  return vazio(204);
}
