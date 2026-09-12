import { afterEach, describe, expect, it, vi } from "vitest";

const readinessState = vi.hoisted(() => vi.fn());

vi.mock("@/lib/readiness.server", () => ({ readinessState }));

/**
 * O `startup` é o endereço que o `HEALTHCHECK` do contêiner sonda, e responde a
 * uma pergunta diferente da do `ready`: não "dá para me mandar tráfego agora",
 * e sim "esta task chegou a servir alguma vez".
 */
describe("web /api/health/startup", () => {
  afterEach(() => {
    vi.resetModules();
    readinessState.mockReset();
    delete (globalThis as Record<symbol, unknown>)[Symbol.for("appsell.web.everReady")];
  });

  it("reprova enquanto a task nunca ficou pronta", async () => {
    // Task nova e quebrada não pode ser promovida num deploy: é o que deixou
    // todo PDF abrir sem botão em 30/08/2026.
    readinessState.mockResolvedValue({ ready: false, redis: true, api: false, storageUpload: true });
    const { GET } = await import("@/app/api/health/startup/route");

    const response = await GET();

    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ status: "starting", ever_ready: false });
  });

  it("aprova depois de ter ficado pronta uma vez, mesmo com dependência fora", async () => {
    // A outra metade, e a que faltava em 12/09/2026: a API caiu em laço de
    // reinício e derrubou o `web` junto, que servia catálogo e páginas públicas
    // o tempo todo. Quem reporta degradação em regime é o `ready`.
    readinessState.mockResolvedValueOnce({
      ready: true,
      redis: true,
      api: true,
      storageUpload: true
    });
    const { GET } = await import("@/app/api/health/startup/route");

    const primeira = await GET();
    readinessState.mockResolvedValue({ ready: false, redis: true, api: false, storageUpload: true });
    const depois = await GET();

    expect(primeira.status).toBe(200);
    expect(depois.status).toBe(200);
    expect(readinessState).toHaveBeenCalledTimes(1);
  });
});
