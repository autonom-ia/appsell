import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MAX_REPORT_BATCH_BYTES,
  MAX_REPORT_BYTES,
  MAX_REPORTS_PER_WINDOW,
  MAX_VIOLATIONS_PER_REQUEST,
  RATE_WINDOW_MS,
  criarLimite,
  cspReportOnlyPolicy,
  parseCspReports
} from "@/lib/csp-report";
import nextConfig from "../next.config";

const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-";

function pedido(corpo: string | ReadableStream<Uint8Array>, tipo: string | null = "application/csp-report", tamanho?: number) {
  const cabecalhos: Record<string, string> = {};
  if (tipo !== null) cabecalhos["content-type"] = tipo;
  if (tamanho !== undefined) cabecalhos["content-length"] = String(tamanho);
  return new Request("https://sell.test/api/csp-report", {
    method: "POST",
    headers: cabecalhos,
    body: corpo,
    duplex: "half"
  } as RequestInit);
}

// Lote da Reporting API com `quantidade` violações iguais.
function lote(quantidade: number, corpo: Record<string, unknown> = {}) {
  return JSON.stringify(Array.from({ length: quantidade }, () => ({
    type: "csp-violation",
    body: {
      documentURL: "https://sell.test/s/hub2you",
      effectiveDirective: "img-src",
      blockedURL: "https://storage.test/appsell-ready/capa.jpg",
      disposition: "report",
      ...corpo
    }
  })));
}

const violacaoDoLote = {
  level: "warn",
  marker: "appsell.csp.violation",
  directive: "img-src",
  blocked: "https://storage.test/appsell-ready/capa.jpg",
  document_path: "/s/hub2you",
  source: "",
  line: null,
  column: null,
  disposition: "report"
};

async function rota() {
  vi.resetModules();
  return (await import("../app/api/csp-report/route")).POST;
}

function linhasDoLog(espiao: { mock: { calls: unknown[][] } }) {
  return espiao.mock.calls.map((chamada) => JSON.parse(String(chamada[0])) as Record<string, unknown>);
}

const relatoAntigo = {
  "csp-report": {
    "document-uri": `https://sell.test/library/access-links/${TOKEN}?email=ana@exemplo.test#x`,
    "violated-directive": "img-src 'self' data: blob:",
    "effective-directive": "img-src",
    "blocked-uri": `https://storage.test/appsell-ready/capa.jpg?X-Amz-Signature=${TOKEN}&X-Amz-Credential=chave`,
    "source-file": "https://sell.test/_next/static/chunks/app.js?v=1",
    "line-number": 12,
    "column-number": 7,
    "script-sample": "ana@exemplo.test",
    disposition: "report"
  }
};

describe("política de conteúdo só-relatório", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("sai em produção, sem bloquear, e aponta para o próprio endpoint", async () => {
    vi.stubEnv("NODE_ENV", "production");
    if (!nextConfig.headers) throw new Error("Next.js security headers are missing.");
    const global = (await nextConfig.headers()).find((regra) => regra.source === "/:path*");
    const nomes = global?.headers.map((cabecalho) => cabecalho.key) ?? [];
    expect(nomes).toContain("Content-Security-Policy-Report-Only");
    expect(nomes).not.toContain("Content-Security-Policy");
    expect(global?.headers).toContainEqual({ key: "Reporting-Endpoints", value: 'csp="/api/csp-report"' });
    expect(cspReportOnlyPolicy()).toContain("report-uri /api/csp-report");
    expect(cspReportOnlyPolicy()).toContain("frame-ancestors 'none'");
  });

  it("fica fora do next dev", async () => {
    vi.stubEnv("NODE_ENV", "development");
    if (!nextConfig.headers) throw new Error("Next.js security headers are missing.");
    const global = (await nextConfig.headers()).find((regra) => regra.source === "/:path*");
    expect(global?.headers.map((cabecalho) => cabecalho.key)).not.toContain("Content-Security-Policy-Report-Only");
  });
});

describe("leitura dos relatos", () => {
  it("tira query string, fragmento, credencial e o trecho do script", () => {
    const [violacao] = parseCspReports(relatoAntigo);
    expect(violacao).toEqual({
      directive: "img-src",
      blocked: "https://storage.test/appsell-ready/capa.jpg",
      document_path: "/library/access-links/<REDACTED>",
      source: "https://sell.test/_next/static/chunks/app.js",
      line: 12,
      column: 7,
      disposition: "report"
    });
    expect(JSON.stringify(violacao)).not.toContain("ana@exemplo.test");
  });

  it("lê o formato da Reporting API e ignora o que não é violação", () => {
    const relatos = parseCspReports([
      { type: "deprecation", body: { message: "x" } },
      {
        type: "csp-violation",
        body: {
          documentURL: "https://sell.test/lojas/hub2you?utm=1",
          effectiveDirective: "script-src-elem",
          blockedURL: "inline",
          disposition: "report"
        }
      }
    ]);
    expect(relatos).toEqual([{
      directive: "script-src-elem",
      blocked: "inline",
      document_path: "/lojas/hub2you",
      source: "",
      line: null,
      column: null,
      disposition: "report"
    }]);
  });

  it("mascara o token do link de acesso no caminho, mesmo curto", () => {
    const [violacao] = parseCspReports({
      "csp-report": { "document-uri": "https://sell.test/c/abc123", "effective-directive": "img-src" }
    });
    expect(violacao?.document_path).toBe("/c/<token>");
    const [fixa] = parseCspReports({
      "csp-report": { "document-uri": "https://sell.test/c/continuar", "effective-directive": "img-src" }
    });
    expect(fixa?.document_path).toBe("/c/continuar");
  });

  it("trata base64 com + e = e percent-encoding como credencial", () => {
    const base64 = "QWxhZGRpbjpvcGVuIHNlc2FtZQ+abcdEFGHijklMNOP==";
    const percentual = "abc%2Fdef%3D0123456789abcdef0123456789";
    const [violacao] = parseCspReports({
      "csp-report": {
        "document-uri": `https://sell.test/library/${base64}/x/${percentual}`,
        "effective-directive": "img-src"
      }
    });
    expect(violacao?.document_path).toBe("/library/<REDACTED>/x/<REDACTED>");
  });

  it("devolve vazio para corpo que não é relato", () => {
    expect(parseCspReports({ qualquer: 1 })).toEqual([]);
    expect(parseCspReports("texto")).toEqual([]);
  });
});

describe("POST /api/csp-report", () => {
  afterEach(() => vi.restoreAllMocks());

  it("grava uma linha limpa por violação: nada de e-mail, token ou query no que sai", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const resposta = await (await rota())(pedido(JSON.stringify(relatoAntigo)) as never);
    expect(resposta.status).toBe(204);
    const saida = log.mock.calls.map((chamada) => String(chamada[0])).join("\n");
    expect(saida).not.toContain("ana@exemplo.test");
    expect(saida).not.toContain(TOKEN);
    expect(saida).not.toContain("X-Amz");
    expect(saida).not.toContain("?");
    expect(linhasDoLog(log)).toEqual([{
      level: "warn",
      marker: "appsell.csp.violation",
      directive: "img-src",
      blocked: "https://storage.test/appsell-ready/capa.jpg",
      document_path: "/library/access-links/<REDACTED>",
      source: "https://sell.test/_next/static/chunks/app.js",
      line: 12,
      column: 7,
      disposition: "report"
    }]);
  });

  it("recusa pelo content-length declarado, mesmo com corpo pequeno", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    const corpo = JSON.stringify(relatoAntigo);
    const requisicao = pedido(corpo, "application/csp-report", MAX_REPORT_BYTES + 1);
    expect(requisicao.headers.get("content-length")).toBe(String(MAX_REPORT_BYTES + 1));
    expect((await POST(requisicao as never)).status).toBe(413);
    expect(log).not.toHaveBeenCalled();
  });

  it("recusa corpo grande, mesmo sem content-length", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    const grande = "x".repeat(MAX_REPORT_BYTES + 1);
    const requisicao = pedido(grande);
    expect(requisicao.headers.get("content-length")).toBeNull();
    expect((await POST(requisicao as never)).status).toBe(413);
    expect(log).not.toHaveBeenCalled();
  });

  it("aceita os três tipos de relato e recusa o resto, inclusive sem content-type", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    expect((await POST(pedido(JSON.stringify(relatoAntigo), "application/csp-report") as never)).status).toBe(204);
    expect((await POST(pedido(lote(1), "application/reports+json") as never)).status).toBe(204);
    expect((await POST(pedido(JSON.stringify(relatoAntigo), "application/json; charset=utf-8") as never)).status).toBe(204);
    expect((await POST(pedido("{}", "text/plain") as never)).status).toBe(415);
    expect((await POST(pedido("{}", null) as never)).status).toBe(415);
  });

  it("aceita lote da Reporting API acima de 8 KiB, até 64 KiB, e grava no máximo 20 linhas", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    const corpo = lote(60);
    expect(corpo.length).toBeGreaterThan(MAX_REPORT_BYTES);
    expect(corpo.length).toBeLessThan(MAX_REPORT_BATCH_BYTES);
    expect((await POST(pedido(corpo, "application/reports+json") as never)).status).toBe(204);
    expect(linhasDoLog(log)).toEqual(Array.from({ length: MAX_VIOLATIONS_PER_REQUEST }, () => violacaoDoLote));
  });

  it("o teto de 64 KiB vale só para reports+json", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    expect((await POST(pedido(lote(60), "application/json") as never)).status).toBe(413);
    const enorme = lote(Math.ceil(MAX_REPORT_BATCH_BYTES / 150));
    expect(enorme.length).toBeGreaterThan(MAX_REPORT_BATCH_BYTES);
    expect((await POST(pedido(enorme, "application/reports+json") as never)).status).toBe(413);
    expect(log).not.toHaveBeenCalled();
  });

  it("conta por violação entre pedidos e solta a linha dropped na janela seguinte", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const relogio = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    const POST = await rota();
    const pedidos = MAX_REPORTS_PER_WINDOW / MAX_VIOLATIONS_PER_REQUEST;
    for (let vez = 0; vez < pedidos; vez += 1) {
      expect((await POST(pedido(lote(25), "application/reports+json") as never)).status).toBe(204);
    }
    expect((await POST(pedido(lote(3), "application/reports+json") as never)).status).toBe(204);
    expect(log).toHaveBeenCalledTimes(MAX_REPORTS_PER_WINDOW);

    log.mockClear();
    relogio.mockReturnValue(1_000_000 + RATE_WINDOW_MS);
    expect((await POST(pedido(lote(1), "application/reports+json") as never)).status).toBe(204);
    // 5 acima do teto em cada um dos 6 pedidos, mais os 3 do pedido sem vaga.
    expect(linhasDoLog(log)).toEqual([
      { level: "warn", marker: "appsell.csp.dropped", dropped: pedidos * 5 + 3 },
      violacaoDoLote
    ]);
  });

  it("não deixa quebra de linha nem ANSI passar para o log", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    const hostil = lote(1, {
      effectiveDirective: "img-src\n{\"marker\":\"falso\"}",
      disposition: "report\u001b[31m\r\nlinha falsa",
      sourceFile: "https://sell.test/x\u001b[2J.js\nlinha"
    });
    expect((await POST(pedido(hostil, "application/reports+json") as never)).status).toBe(204);
    expect(log).toHaveBeenCalledTimes(1);
    const linha = String(log.mock.calls[0]?.[0]);
    expect(linha).not.toContain("\n");
    expect(linha).not.toContain("\r");
    expect(linha).not.toContain("\u001b");
    expect(linhasDoLog(log)[0]?.marker).toBe("appsell.csp.violation");
  });

  it("tira userinfo de blocked e mascara /c/ em blocked e source", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    const corpo = lote(1, {
      blockedURL: "https://ana:senha@storage.test/c/abc123?x=1",
      sourceFile: "https://sell.test/c/xyz789"
    });
    expect((await POST(pedido(corpo, "application/reports+json") as never)).status).toBe(204);
    const saida = String(log.mock.calls[0]?.[0]);
    expect(saida).not.toContain("ana");
    expect(saida).not.toContain("senha");
    expect(saida).not.toContain("abc123");
    expect(saida).not.toContain("xyz789");
    expect(linhasDoLog(log)).toEqual([{
      ...violacaoDoLote,
      blocked: "https://storage.test/c/<token>",
      source: "https://sell.test/c/<token>"
    }]);
  });

  it("responde 400 quando o cliente aborta o envio", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    const abortado = new ReadableStream<Uint8Array>({
      start(controle) {
        controle.enqueue(new TextEncoder().encode("{\"csp-report\":"));
        controle.error(new Error("conexão fechada"));
      }
    });
    expect((await POST(pedido(abortado) as never)).status).toBe(400);
    expect(log).not.toHaveBeenCalled();
  });

  it("responde 400 para JSON quebrado, sem log", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    expect((await POST(pedido("{quebrado") as never)).status).toBe(400);
    expect(log).not.toHaveBeenCalled();
  });
});

describe("limite de taxa", () => {
  it("descarta o excesso da janela e conta os descartes na janela seguinte", () => {
    const limite = criarLimite(2, 1_000);
    expect(limite.aceitar(0, 1).aceitos).toBe(1);
    expect(limite.aceitar(10, 1).aceitos).toBe(1);
    expect(limite.aceitar(20, 1).aceitos).toBe(0);
    expect(limite.aceitar(30, 1).aceitos).toBe(0);
    expect(limite.aceitar(1_000, 1)).toEqual({ aceitos: 1, descartadosNaJanelaAnterior: 2 });
  });

  it("desconta por violação e conta o que passa do teto do pedido", () => {
    const limite = criarLimite(10, 1_000);
    expect(limite.aceitar(0, 8, 3)).toEqual({ aceitos: 3, descartadosNaJanelaAnterior: 0 });
    expect(limite.aceitar(1, 12, 20)).toEqual({ aceitos: 7, descartadosNaJanelaAnterior: 0 });
    expect(limite.aceitar(2, 0)).toEqual({ aceitos: 0, descartadosNaJanelaAnterior: 0 });
    expect(limite.aceitar(1_000, 0)).toEqual({ aceitos: 0, descartadosNaJanelaAnterior: 10 });
  });
});
