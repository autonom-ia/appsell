import { NextRequest, NextResponse } from "next/server";

import { MAX_REPORT_BYTES, criarLimite, parseCspReports } from "@/lib/csp-report";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Relatos da política em modo só-relatório (#548). Não guarda nada além de uma
// linha de log por violação, com os campos já limpos em lib/csp-report.ts. A
// retenção é a do log do serviço: docs/runbooks/csp-relatorio.md.
const TIPOS_ACEITOS = new Set(["application/csp-report", "application/reports+json", "application/json"]);
const SEM_CACHE = { "cache-control": "no-store" };
const limite = criarLimite();

export async function POST(request: NextRequest) {
  const tipo = (request.headers.get("content-type") ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
  if (!TIPOS_ACEITOS.has(tipo)) {
    return new NextResponse(null, { status: 415, headers: SEM_CACHE });
  }
  const declarado = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declarado) && declarado > MAX_REPORT_BYTES) {
    return new NextResponse(null, { status: 413, headers: SEM_CACHE });
  }
  const corpo = await lerComLimite(request);
  if (corpo === null) {
    return new NextResponse(null, { status: 413, headers: SEM_CACHE });
  }

  const vez = limite.aceitar(Date.now());
  if (vez.descartadosNaJanelaAnterior > 0) {
    console.warn(JSON.stringify({
      level: "warn",
      marker: "appsell.csp.dropped",
      dropped: vez.descartadosNaJanelaAnterior
    }));
  }
  if (!vez.aceito) {
    return new NextResponse(null, { status: 204, headers: SEM_CACHE });
  }

  let dados: unknown;
  try {
    dados = JSON.parse(corpo);
  } catch {
    return new NextResponse(null, { status: 400, headers: SEM_CACHE });
  }
  for (const violacao of parseCspReports(dados)) {
    console.warn(JSON.stringify({ level: "warn", marker: "appsell.csp.violation", ...violacao }));
  }
  return new NextResponse(null, { status: 204, headers: SEM_CACHE });
}

// O content-length pode faltar ou mentir: o limite vale para o que chega.
async function lerComLimite(request: NextRequest): Promise<string | null> {
  if (!request.body) return "";
  const leitor = request.body.getReader();
  const pedacos: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await leitor.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_REPORT_BYTES) {
      await leitor.cancel();
      return null;
    }
    pedacos.push(value);
  }
  return new TextDecoder().decode(Buffer.concat(pedacos));
}
