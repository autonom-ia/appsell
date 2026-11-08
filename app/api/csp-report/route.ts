import { NextRequest, NextResponse } from "next/server";

import { MAX_VIOLATIONS_PER_REQUEST, criarLimite, maxBytesFor, parseCspReports } from "@/lib/csp-report";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Relatos da política em modo só-relatório (#548). Não guarda nada além de uma
// linha de log por violação, com os campos já limpos em lib/csp-report.ts. A
// retenção é a do log do serviço: docs/runbooks/csp-relatorio.md.
const TIPOS_ACEITOS = new Set(["application/csp-report", "application/reports+json", "application/json"]);
const SEM_CACHE = { "cache-control": "no-store" };
const limite = criarLimite();

function vazio(status: number) {
  return new NextResponse(null, { status, headers: SEM_CACHE });
}

export async function POST(request: NextRequest) {
  const tipo = (request.headers.get("content-type") ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
  if (!TIPOS_ACEITOS.has(tipo)) return vazio(415);
  const maximo = maxBytesFor(tipo);
  const declarado = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declarado) && declarado > maximo) return vazio(413);

  const leitura = await lerComLimite(request, maximo);
  if ("erro" in leitura) return vazio(leitura.erro);

  let dados: unknown;
  try {
    dados = JSON.parse(leitura.corpo);
  } catch {
    return vazio(400);
  }
  const violacoes = parseCspReports(dados);
  const vez = limite.aceitar(Date.now(), violacoes.length, MAX_VIOLATIONS_PER_REQUEST);
  if (vez.descartadosNaJanelaAnterior > 0) {
    console.warn(JSON.stringify({
      level: "warn",
      marker: "appsell.csp.dropped",
      dropped: vez.descartadosNaJanelaAnterior
    }));
  }
  for (const violacao of violacoes.slice(0, vez.aceitos)) {
    console.warn(JSON.stringify({ level: "warn", marker: "appsell.csp.violation", ...violacao }));
  }
  return vazio(204);
}

// O content-length pode faltar ou mentir: o limite vale para o que chega. Se o
// navegador fecha a conexão no meio do envio, a leitura falha e o pedido
// recebe 400, não 500.
async function lerComLimite(
  request: NextRequest,
  maximo: number
): Promise<{ corpo: string } | { erro: 400 | 413 }> {
  if (!request.body) return { corpo: "" };
  const leitor = request.body.getReader();
  const pedacos: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await leitor.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximo) {
        await leitor.cancel();
        return { erro: 413 };
      }
      pedacos.push(value);
    }
  } catch {
    return { erro: 400 };
  }
  return { corpo: new TextDecoder().decode(Buffer.concat(pedacos)) };
}
