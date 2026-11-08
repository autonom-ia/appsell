import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/lib/internal-api-client.server", () => ({ internalServiceConfig: vi.fn() }));

import { atribuicaoFalhou } from "../lib/tracking/checkout-attribution.server";
import { registrarFalha } from "../lib/tracking/servico.server";

// #563: o log de falha do rastreio roda no caminho do checkout. Ele nunca pode
// lançar, ou uma falha do log viraria falha da compra.
describe("log de falha do rastreio", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("registra só caminho, motivo, nome do erro e status", () => {
    const saida = vi.spyOn(console, "error").mockImplementation(() => undefined);

    registrarFalha("/v1/public/tracking/checkout-attribution", "unexpected", new TypeError("ref-123"), 502);

    expect(saida).toHaveBeenCalledTimes(1);
    const linha = JSON.parse(String(saida.mock.calls[0][0])) as Record<string, unknown>;
    expect(linha).toEqual({
      level: "error",
      marker: "appsell.tracking.unavailable",
      path: "/v1/public/tracking/checkout-attribution",
      reason: "unexpected",
      error_name: "TypeError",
      upstream_status: 502
    });
    expect(String(saida.mock.calls[0][0])).not.toContain("ref-123");
  });

  it("não lança quando a saída do log lança", () => {
    vi.spyOn(console, "error").mockImplementation(() => {
      throw new Error("stderr fechado");
    });

    expect(() => registrarFalha("/x", "unexpected", new Error("boom"), null)).not.toThrow();
    expect(() => atribuicaoFalhou(new Error("boom"))).not.toThrow();
  });

  it("não lança com um erro cujo nome lança ao ser lido", () => {
    const saida = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const erroHostil = new Error("boom");
    Object.defineProperty(erroHostil, "name", {
      get() {
        throw new Error("getter hostil");
      }
    });

    expect(() => atribuicaoFalhou(erroHostil)).not.toThrow();
    const linha = JSON.parse(String(saida.mock.calls[0][0])) as Record<string, unknown>;
    expect(linha.error_name).toBeNull();
  });

  it("não lança com um valor que não é Error", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    expect(() => atribuicaoFalhou(Symbol("estranho"))).not.toThrow();
    expect(() => atribuicaoFalhou(undefined)).not.toThrow();
  });
});
