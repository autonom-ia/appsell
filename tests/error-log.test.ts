import { afterEach, describe, expect, it, vi } from "vitest";

import { onRequestError } from "../instrumentation";
import { redigirTexto } from "@/lib/redacao";

const EMAIL = "ana.silva+loja@exemplo.com.br";
const CPF = "529.982.247-25";
const CNPJ = "11.222.333/0001-81";
const LINK = "c0ffee".repeat(10) + "abcd";
const VALOR_CURTO = "abc123";

function chamar(error: unknown, path = `/c/${LINK}?email=${EMAIL}`) {
  const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
  void onRequestError(
    error,
    { path, method: "GET", headers: { authorization: `Bearer ${VALOR_CURTO}`, cookie: `sid=${VALOR_CURTO}` } },
    { routerKind: "App Router", routePath: "/c/[token]", routeType: "render", renderSource: "react-server-components" } as never
  );
  expect(log).toHaveBeenCalledTimes(1);
  return String(log.mock.calls[0]?.[0]);
}

describe("erro de requisição do web vai para o log redigido (#549)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sai uma linha JSON de erro, sem dado pessoal nem segredo no que é escrito", () => {
    const erro = new Error(
      `envio para ${EMAIL} falhou; cpf ${CPF}; cnpj ${CNPJ}; ` +
      `GET https://sell.test/c/${LINK}?token=${VALOR_CURTO}; ` +
      `headers={"authorization": "Bearer ${VALOR_CURTO}", "cookie": "sid=${VALOR_CURTO}"}`
    );
    erro.stack = `Error: ${erro.message}\n    at fetch (https://sell.test/api/x?email=${EMAIL}:1:2)`;

    const linha = chamar(erro);

    for (const vazado of [EMAIL, CPF, CNPJ, LINK, VALOR_CURTO]) expect(linha).not.toContain(vazado);
    const registro = JSON.parse(linha) as Record<string, unknown>;
    expect(registro).toMatchObject({
      level: "error",
      marker: "appsell.web.request_error",
      method: "GET",
      route_path: "/c/[token]",
      error_type: "Error"
    });
    expect(String(registro.message)).toContain("envio para <REDACTED> falhou");
    expect(String(registro.stack)).toContain("https://sell.test/api/x?<REDACTED>");
  });

  it("não grava o caminho concreto nem os cabeçalhos do pedido", () => {
    const linha = chamar(new Error("boom"));
    expect(linha).not.toContain("?email");
    expect(linha).not.toContain("authorization");
    expect(linha).not.toContain("cookie");
  });

  it("aguenta erro que não é Error", () => {
    const registro = JSON.parse(chamar(`falhou para ${EMAIL}`)) as Record<string, unknown>;
    expect(registro.error_type).toBe("string");
    expect(registro.message).toBe("falhou para <REDACTED>");
    expect(registro.stack).toBeNull();
  });
});

describe("regras do redator do web", () => {
  it.each([
    [`para ${EMAIL}.`, "para <REDACTED>"],
    ["cpf 52998224725", "cpf <REDACTED>"],
    ["cpf 111.111.111-11", "cpf 111.111.111-11"],
    [`cnpj ${CNPJ}`, "cnpj <REDACTED>"],
    ["Authorization: Bearer x\nseguinte", "Authorization: <REDACTED>\nseguinte"],
    ['{"cookie": "a=b; c=d", "accept": "*/*"}', '{"cookie": "<REDACTED>", "accept": "*/*"}'],
    ["https://h.test/a?b=c fim", "https://h.test/a?<REDACTED> fim"],
    ["https://h.test/a?<REDACTED> fim", "https://h.test/a?<REDACTED> fim"],
    [`link ${LINK}`, "link <REDACTED>"]
  ])("%s", (entrada, esperado) => {
    expect(redigirTexto(entrada)).toBe(esperado);
  });

  it.each([
    "550e8400-e29b-41d4-a716-446655440000",
    "curso-de-inteligencia-artificial-na-pratica-2026",
    "2026-11-05T10:00:16.123Z",
    "pedido 12345678901",
    "x-authorization-hint: ok",
    "usuario@sem-dominio"
  ])("mantém %s", (valor) => {
    expect(redigirTexto(valor)).toBe(valor);
  });
});
