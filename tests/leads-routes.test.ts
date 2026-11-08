import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const { readDomainStoreLeads, exportDomainStoreLeads, authenticatedSession, DomainApiError } =
  vi.hoisted(() => {
    class DomainApiError extends Error {
      constructor(readonly status: number, readonly code: string) {
        super(code);
      }
    }
    return {
      readDomainStoreLeads: vi.fn(),
      exportDomainStoreLeads: vi.fn(),
      authenticatedSession: vi.fn(),
      DomainApiError
    };
  });

vi.mock("@/lib/internal-api-client.server", () => ({
  readDomainStoreLeads,
  exportDomainStoreLeads,
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

import { GET as dados } from "../app/(protected)/dashboard/leads/dados/route";
import { GET as csv } from "../app/(protected)/dashboard/leads/csv/route";

const LOJA = "44444444-4444-4444-8444-444444444444";
const PRODUTO = "55555555-5555-4555-8555-555555555555";
const BASE = "https://sell.test/dashboard/leads";
const PERIODO = "from=2026-10-01&to=2026-10-31";

function pedido(caminho: string, query: string, headers: Record<string, string> = {}) {
  return new NextRequest(`${BASE}/${caminho}?${query}`, { headers });
}

describe("rotas da base de leads", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("APPSELL_LEADS_PILOT_SELLER_IDS", LOJA);
    authenticatedSession.mockResolvedValue({ sessionId: "s", session: { subject: "u" } });
    readDomainStoreLeads.mockResolvedValue({ schema_version: 1, rows: [] });
    exportDomainStoreLeads.mockResolvedValue({
      schema_version: 1,
      rows: [{
        name: "=HYPERLINK(1)",
        email: "lead@example.test",
        product_title: "Curso, avançado",
        source: "billing",
        access_status: "active",
        since: "2026-10-05"
      }]
    });
  });

  it("sem sessão responde 401", async () => {
    authenticatedSession.mockResolvedValue(null);
    expect((await dados(pedido("dados", `seller=${LOJA}&${PERIODO}`))).status).toBe(401);
  });

  it("piloto vazio responde 404 e não chama a API", async () => {
    vi.stubEnv("APPSELL_LEADS_PILOT_SELLER_IDS", "");
    expect((await dados(pedido("dados", `seller=${LOJA}&${PERIODO}`))).status).toBe(404);
    expect((await csv(pedido("csv", `seller=${LOJA}&${PERIODO}`))).status).toBe(404);
    expect(readDomainStoreLeads).not.toHaveBeenCalled();
    expect(exportDomainStoreLeads).not.toHaveBeenCalled();
  });

  it("o piloto de vendas não libera a base de leads", async () => {
    vi.stubEnv("APPSELL_LEADS_PILOT_SELLER_IDS", "");
    vi.stubEnv("APPSELL_SALES_REPORT_PILOT_SELLER_IDS", LOJA);
    expect((await dados(pedido("dados", `seller=${LOJA}&${PERIODO}`))).status).toBe(404);
  });

  it("loja, período ou produto inválidos respondem 422 sem chamar a API", async () => {
    for (const query of [
      `seller=nao-uuid&${PERIODO}`,
      `seller=${LOJA}&from=2026-10-31&to=2026-10-01`,
      `seller=${LOJA}&from=2025-10-01&to=2026-10-02`,
      `seller=${LOJA}&${PERIODO}&product=nao-uuid`
    ]) {
      expect((await dados(pedido("dados", query))).status).toBe(422);
    }
    expect(readDomainStoreLeads).not.toHaveBeenCalled();
  });

  it("repassa a leitura com no-store e o produto do filtro", async () => {
    const resposta = await dados(pedido("dados", `seller=${LOJA}&${PERIODO}&product=${PRODUTO}`));
    expect(resposta.status).toBe(200);
    expect(resposta.headers.get("cache-control")).toBe("no-store");
    expect(readDomainStoreLeads).toHaveBeenCalledWith("s", { subject: "u" }, LOJA, {
      from: "2026-10-01",
      to: "2026-10-31",
      product: PRODUTO
    });
  });

  it("o CSV de marketing sai como anexo, sem cache, com BOM e a fórmula neutralizada", async () => {
    const resposta = await csv(pedido("csv", `seller=${LOJA}&${PERIODO}`));
    expect(resposta.status).toBe(200);
    expect(resposta.headers.get("content-disposition")).toBe(
      'attachment; filename="leads-marketing-2026-10-01-a-2026-10-31.csv"'
    );
    expect(resposta.headers.get("cache-control")).toBe("no-store");
    const bytes = new Uint8Array(await resposta.arrayBuffer());
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    const texto = new TextDecoder().decode(bytes.slice(3));
    expect(texto).toBe(
      "nome,email,produto,como_entrou,acesso,desde\r\n" +
      "'=HYPERLINK(1),lead@example.test,\"Curso, avançado\",compra,ativo,2026-10-05\r\n"
    );
  });

  it("o CSV recusa pedido vindo de outro site e não gasta a cota", async () => {
    for (const origem of ["cross-site", "same-site"]) {
      const resposta = await csv(pedido("csv", `seller=${LOJA}&${PERIODO}`, { "sec-fetch-site": origem }));
      expect(resposta.status).toBe(403);
    }
    expect(exportDomainStoreLeads).not.toHaveBeenCalled();
  });

  it("limite de taxa da API vira 429", async () => {
    exportDomainStoreLeads.mockRejectedValue(new DomainApiError(429, "export_rate_limited"));
    expect((await csv(pedido("csv", `seller=${LOJA}&${PERIODO}`))).status).toBe(429);
  });
});
