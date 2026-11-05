import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/lib/oauth-service.server", () => ({ oauthService: () => ({ health: async () => true }) }));
vi.mock("@/lib/storage-upload-policy.server", () => ({ storageUploadConfigurationReady: () => true }));

/**
 * Um mapa de produtos que não parseia derruba todo pagamento do checkout local
 * (#148): o webhook não acha produto nenhum. O `ready` precisa reprovar, para o
 * deploy não promover a task. Com `financial_checkout` o mapa não decide venda
 * nem entrega, e reprovar ali tiraria do ar uma loja que vende pelo Financial.
 */
describe("web readiness: mapa de produtos do checkout", () => {
  const originalEnvironment = { ...process.env };

  beforeEach(() => {
    process.env.APPSELL_DOMAIN_API_URL = "http://api.test";
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: "ok", database: true })));
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  async function ready() {
    const { GET } = await import("@/app/api/health/ready/route");
    const response = await GET();
    return { status: response.status, body: await response.json() as Record<string, unknown> };
  }

  it("reprova com o checkout local e o mapa inválido", async () => {
    process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{not json";

    expect(await ready()).toEqual({
      status: 503,
      body: expect.objectContaining({ status: "unavailable", checkout_map: false })
    });
  });

  it("aprova com o checkout local e o mapa válido", async () => {
    process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      "loja/curso": { financial_product_key: "prod_curso" }
    });

    expect(await ready()).toEqual({ status: 200, body: expect.objectContaining({ status: "ok", checkout_map: true }) });
  });

  it("não reprova pelo mapa quando a venda passa pelo Financial", async () => {
    process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{not json";

    expect(await ready()).toEqual({ status: 200, body: expect.objectContaining({ status: "ok", checkout_map: true }) });
  });
});
