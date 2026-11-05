import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const { readDomainSalesReport, exportDomainSalesRows, authenticatedSession, DomainApiError } =
  vi.hoisted(() => {
    class DomainApiError extends Error {
      constructor(readonly status: number, readonly code: string) {
        super(code);
      }
    }
    return {
      readDomainSalesReport: vi.fn(),
      exportDomainSalesRows: vi.fn(),
      authenticatedSession: vi.fn(),
      DomainApiError
    };
  });

vi.mock("@/lib/internal-api-client.server", () => ({
  readDomainSalesReport,
  exportDomainSalesRows,
  DomainApiError
}));
vi.mock("@/app/api/sellers/[sellerId]/products/[productId]/_route-shared", () => ({
  authenticatedSession,
  canonicalUuid: (value: string | undefined) =>
    value !== undefined && value.length === 36 && value.split("-").length === 5 ? value : null,
  mappedError: (error: unknown) =>
    new Response(JSON.stringify({ error: error instanceof DomainApiError ? error.code : "x" }), {
      status: error instanceof DomainApiError ? error.status : 503
    })
}));

import { GET as dados } from "../app/(protected)/dashboard/vendas/dados/route";
import { GET as csv } from "../app/(protected)/dashboard/vendas/csv/route";

const LOJA = "44444444-4444-4444-8444-444444444444";
const BASE = "https://sell.test/dashboard/vendas";

function pedido(caminho: string, query: string, headers: Record<string, string> = {}) {
  return new NextRequest(`${BASE}/${caminho}?${query}`, { headers });
}

describe("rotas do relatório de vendas", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("APPSELL_SALES_REPORT_PILOT_SELLER_IDS", LOJA);
    authenticatedSession.mockResolvedValue({ sessionId: "s", session: { subject: "u" } });
    readDomainSalesReport.mockResolvedValue({ schema_version: 1, rows: [] });
    exportDomainSalesRows.mockResolvedValue({
      schema_version: 1,
      rows: [{ sold_on: "2026-10-01", refunded_on: null, product_title: "@x", status: "approved", purchase_ref: "abcdef012345" }]
    });
  });

  it("sem sessão responde 401", async () => {
    authenticatedSession.mockResolvedValue(null);
    const resposta = await dados(pedido("dados", `seller=${LOJA}&from=2026-10-01&to=2026-10-31`));
    expect(resposta.status).toBe(401);
  });

  it("loja fora do piloto responde 404 e não chama a API", async () => {
    vi.stubEnv("APPSELL_SALES_REPORT_PILOT_SELLER_IDS", "");
    const resposta = await dados(pedido("dados", `seller=${LOJA}&from=2026-10-01&to=2026-10-31`));
    expect(resposta.status).toBe(404);
    expect(readDomainSalesReport).not.toHaveBeenCalled();
  });

  it("período ou loja inválidos respondem 422 sem chamar a API", async () => {
    for (const query of [
      `seller=nao-uuid&from=2026-10-01&to=2026-10-31`,
      `seller=${LOJA}&from=2026-10-31&to=2026-10-01`,
      `seller=${LOJA}&from=2026-10-01`,
      // 367 dias de calendário, contando as duas pontas.
      `seller=${LOJA}&from=2025-10-01&to=2026-10-02`
    ]) {
      expect((await dados(pedido("dados", query))).status).toBe(422);
    }
    expect(readDomainSalesReport).not.toHaveBeenCalled();
  });

  it("repassa o relatório com no-store", async () => {
    const resposta = await dados(pedido("dados", `seller=${LOJA}&from=2026-10-01&to=2026-10-31`));
    expect(resposta.status).toBe(200);
    expect(resposta.headers.get("cache-control")).toBe("no-store");
    expect(readDomainSalesReport).toHaveBeenCalledWith("s", { subject: "u" }, LOJA, {
      from: "2026-10-01",
      to: "2026-10-31"
    });
  });

  it("o CSV sai como anexo, sem cache, com a fórmula neutralizada", async () => {
    const resposta = await csv(pedido("csv", `seller=${LOJA}&from=2026-10-01&to=2026-10-31`));
    expect(resposta.status).toBe(200);
    expect(resposta.headers.get("content-type")).toBe("text/csv; charset=utf-8");
    expect(resposta.headers.get("content-disposition")).toBe(
      'attachment; filename="vendas-2026-10-01-a-2026-10-31.csv"'
    );
    expect(resposta.headers.get("cache-control")).toBe("no-store");
    expect(await resposta.text()).toContain(",'@x,aprovada,");
  });

  it("366 dias de calendário, contando as duas pontas, ainda passam", async () => {
    const resposta = await dados(pedido("dados", `seller=${LOJA}&from=2025-10-01&to=2026-10-01`));
    expect(resposta.status).toBe(200);
  });

  it("o CSV recusa pedido vindo de outro site e não gasta a cota", async () => {
    for (const origem of ["cross-site", "same-site"]) {
      const resposta = await csv(
        pedido("csv", `seller=${LOJA}&from=2026-10-01&to=2026-10-31`, { "sec-fetch-site": origem })
      );
      expect(resposta.status).toBe(403);
    }
    expect(exportDomainSalesRows).not.toHaveBeenCalled();
  });

  it("o CSV aceita a mesma origem e a navegação digitada pela pessoa", async () => {
    for (const origem of ["same-origin", "none"]) {
      const resposta = await csv(
        pedido("csv", `seller=${LOJA}&from=2026-10-01&to=2026-10-31`, { "sec-fetch-site": origem })
      );
      expect(resposta.status).toBe(200);
    }
  });

  it("limite de taxa da API vira 429", async () => {
    exportDomainSalesRows.mockRejectedValue(new DomainApiError(429, "export_rate_limited"));
    const resposta = await csv(pedido("csv", `seller=${LOJA}&from=2026-10-01&to=2026-10-31`));
    expect(resposta.status).toBe(429);
  });
});
