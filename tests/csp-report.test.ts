import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MAX_REPORT_BYTES,
  criarLimite,
  cspReportOnlyPolicy,
  parseCspReports
} from "@/lib/csp-report";
import nextConfig from "../next.config";

const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-";

function pedido(corpo: string, tipo = "application/csp-report", tamanho?: number) {
  const cabecalhos: Record<string, string> = { "content-type": tipo };
  if (tamanho !== undefined) cabecalhos["content-length"] = String(tamanho);
  return new Request("https://sell.test/api/csp-report", { method: "POST", headers: cabecalhos, body: corpo });
}

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
    expect(linhasDoLog(log)).toEqual([expect.objectContaining({
      level: "warn",
      marker: "appsell.csp.violation",
      directive: "img-src"
    })]);
  });

  it("recusa corpo grande, mesmo sem content-length", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const POST = await rota();
    const grande = "x".repeat(MAX_REPORT_BYTES + 1);
    expect((await POST(pedido(grande, "application/csp-report", grande.length) as never)).status).toBe(413);
    expect((await POST(pedido(grande) as never)).status).toBe(413);
    expect(log).not.toHaveBeenCalled();
  });

  it("recusa tipo que não é relato", async () => {
    const POST = await rota();
    expect((await POST(pedido("{}", "text/plain") as never)).status).toBe(415);
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
    expect(limite.aceitar(0).aceito).toBe(true);
    expect(limite.aceitar(10).aceito).toBe(true);
    expect(limite.aceitar(20).aceito).toBe(false);
    expect(limite.aceitar(30).aceito).toBe(false);
    expect(limite.aceitar(1_000)).toEqual({ aceito: true, descartadosNaJanelaAnterior: 2 });
  });
});
