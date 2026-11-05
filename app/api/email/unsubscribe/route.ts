import { NextRequest, NextResponse } from "next/server";

import { descadastrarPorToken } from "./descadastro.server";
import { dentroDoLimite } from "./limite";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Descadastro de e-mail opcional em um clique, sem login (#561, RFC 8058).
// Fica fora do CSRF de sessão de propósito: não há sessão, o token é a
// autorização, a ação só reduz envio e é idempotente. A resposta é a mesma
// para token válido, desconhecido ou ausente.
const MAX_CORPO_BYTES = 1024;
const TOKEN_MIN = 16;
const TOKEN_MAX = 128;
const CABECALHOS = {
  "cache-control": "no-store",
  "referrer-policy": "no-referrer",
  "x-robots-tag": "noindex"
} as const;

function responder(status: number, corpo: Record<string, unknown>) {
  return NextResponse.json(corpo, { status, headers: CABECALHOS });
}

function tokenValido(valor: unknown): string | null {
  if (typeof valor !== "string") return null;
  if (valor.length < TOKEN_MIN || valor.length > TOKEN_MAX) return null;
  return valor;
}

// O one-click (RFC 8058) chega com o token na query e o corpo
// "List-Unsubscribe=One-Click"; a página manda {"token": ...} em JSON.
function tokenDoPedido(request: NextRequest, corpo: string): string | null {
  const daQuery = tokenValido(request.nextUrl.searchParams.get("t"));
  if (daQuery) return daQuery;
  if (!(request.headers.get("content-type") ?? "").startsWith("application/json")) return null;
  try {
    const dados: unknown = JSON.parse(corpo);
    if (typeof dados !== "object" || dados === null || Array.isArray(dados)) return null;
    return tokenValido((dados as Record<string, unknown>).token);
  } catch {
    return null;
  }
}

function ipDe(request: NextRequest): string {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip") || "desconhecido";
}

// Lê no máximo MAX_CORPO_BYTES + 1 do stream e para: o teto vale mesmo sem
// content-length (chunked) ou com um content-length mentiroso.
async function corpoLimitado(request: NextRequest): Promise<string | null> {
  if (!request.body) return "";
  const leitor = request.body.getReader();
  const partes: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await leitor.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_CORPO_BYTES) return null;
      partes.push(value);
    }
  } finally {
    await leitor.cancel().catch(() => undefined);
  }
  return Buffer.concat(partes).toString("utf8");
}

export async function POST(request: NextRequest) {
  // Limite de taxa antes de ler qualquer byte do corpo.
  if (!dentroDoLimite(ipDe(request))) return responder(429, { error: "muitas_tentativas" });
  const declarado = Number(request.headers.get("content-length") ?? "0");
  if (declarado > MAX_CORPO_BYTES) return responder(413, { error: "corpo_grande" });
  const corpo = await corpoLimitado(request);
  if (corpo === null) return responder(413, { error: "corpo_grande" });

  const token = tokenDoPedido(request, corpo);
  if (token) {
    try {
      await descadastrarPorToken(token);
    } catch {
      // descadastro.server.ts já registrou o motivo, sem o token.
      return responder(503, { error: "descadastro_indisponivel" });
    }
  }
  return responder(200, { ok: true });
}
