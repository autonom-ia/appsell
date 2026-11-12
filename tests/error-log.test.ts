import { afterEach, describe, expect, it, vi } from "vitest";

import { onRequestError, register } from "../instrumentation";
import { instalarConsoleRedigido, linhaRedigida } from "@/lib/console-redigido";
import { limitarTamanho, redigirTexto, TETO_DE_TEXTO } from "@/lib/redacao";

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

describe("revisão do PR #576: formatos que escapavam", () => {
  it.each([
    ["Apartamento 50 m²", "Apartamento 50 m²"],
    ["GET /c/link?token=abc123 HTTP", "GET /c/link?<REDACTED> HTTP"],
    ["https://h.test/a#rt=x fim", "https://h.test/a#<REDACTED> fim"],
    ["http://api.internal:8080?notify=alice@autonomia.site", "http://api.internal:8080?<REDACTED>"],
    ["{ 'set-cookie': [ 'sid=abc123', 'tema=claro' ] }", "{ 'set-cookie': [<REDACTED>] }"],
    ["Map(1) { 'authorization' => 'Bearer abc123' }", "Map(1) { 'authorization' => '<REDACTED>' }"],
    ["(b'authorization', b'Bearer abc123')", "(b'authorization', b'<REDACTED>')"],
    ["Authorization: Bearer\n abc123\nproxima", "Authorization: <REDACTED>\nproxima"],
    ["Authorization: Bearer x\n    at f (a.js:1:2)", "Authorization: <REDACTED>\n    at f (a.js:1:2)"],
    ["authorization, cookie e x-api-key faltaram", "authorization, cookie e x-api-key faltaram"],
    ["josé@gmail.com", "<REDACTED>"],
    ["ana@exémplo.com.br", "<REDACTED>"],
    ["/u/ana%40exemplo.com fim", "/u/<REDACTED> fim"],
    ["ana_silva@exemplo.com", "<REDACTED>"],
    ["pergunta?", "pergunta?"]
  ])("%s", (entrada, esperado) => {
    expect(redigirTexto(entrada)).toBe(esperado);
  });

  it("tem teto antes de redigir e não deixa e-mail pela metade", () => {
    const texto = `${"x ".repeat(9000)}${EMAIL}${" y".repeat(9000)}`;
    const limitado = limitarTamanho(texto, TETO_DE_TEXTO);
    expect(limitado.length).toBeLessThanOrEqual(TETO_DE_TEXTO);
    expect(limitarTamanho(limitado, TETO_DE_TEXTO)).toBe(limitado);
    expect(new Set(limitado.split(" ").filter(Boolean))).toEqual(new Set(["x", "y", "[...truncated...]"]));
  });

  it.each([
    "authorization ".repeat(3000),
    "authorization:\n".repeat(3000),
    "cookie".repeat(7000),
    "http://a/b?c ".repeat(4000),
    "a@b.co ".repeat(6000),
    "'set-cookie': [".repeat(3000)
  ])("texto de ~40 KB redige rápido (%#)", (entrada) => {
    const inicio = performance.now();
    redigirTexto(entrada);
    expect(performance.now() - inicio).toBeLessThan(1000);
  });
});

describe("o console do servidor sai redigido (register)", () => {
  function consoleFalso() {
    const escrito: unknown[][] = [];
    const gravar = (...args: unknown[]) => { escrito.push(args); };
    return { escrito, alvo: { error: gravar, warn: gravar, log: gravar, info: gravar, debug: gravar } };
  }

  it("o erro cru do Next (⨯ + Error) sai sem e-mail nem CPF", () => {
    const { escrito, alvo } = consoleFalso();
    instalarConsoleRedigido(alvo);
    const erro = Object.assign(new Error(`pagina falhou para ${EMAIL} cpf ${CPF}`), { digest: "123" });
    alvo.error("⨯", erro);
    expect(escrito).toHaveLength(1);
    const linha = String(escrito[0]?.[0]);
    expect(linha).not.toContain(EMAIL);
    expect(linha).not.toContain(CPF);
    expect(linha).toContain("⨯ Error: pagina falhou para <REDACTED> cpf <REDACTED>");
    expect(linha).toContain("digest: '123'");
  });

  it("linha JSON continua JSON válido, com cabeçalho e documento numérico redigidos", () => {
    const { escrito, alvo } = consoleFalso();
    instalarConsoleRedigido(alvo);
    alvo.log(JSON.stringify({ level: "error", email: "josé@gmail.com", documento: 52998224725, headers: { "set-cookie": ["sid=abc123"], accept: "*/*" } }));
    const registro = JSON.parse(String(escrito[0]?.[0])) as Record<string, unknown>;
    expect(registro).toEqual({ level: "error", email: "<REDACTED>", documento: "<REDACTED>", headers: { "set-cookie": "<REDACTED>", accept: "*/*" } });
  });

  it("objeto com cabeçalhos sai sem o valor", () => {
    const linha = linhaRedigida(["pedido", { authorization: `Bearer ${VALOR_CURTO}`, cookie: `sid=${VALOR_CURTO}` }]);
    expect(linha).not.toContain(VALOR_CURTO);
  });

  it("não envolve duas vezes", () => {
    const { escrito, alvo } = consoleFalso();
    instalarConsoleRedigido(alvo);
    const depoisDaPrimeira = alvo.error;
    instalarConsoleRedigido(alvo);
    expect(alvo.error).toBe(depoisDaPrimeira);
    alvo.error("ok");
    expect(escrito).toEqual([["ok"]]);
  });

  it("register instala o redator no console global", () => {
    const metodos = ["error", "warn", "log", "info", "debug"] as const;
    const originais = metodos.map((metodo) => console[metodo]);
    const escrito: unknown[] = [];
    console.error = (...args: unknown[]) => { escrito.push(...args); };
    const marca = Symbol.for("appsell.console-redigido");
    const global = console as unknown as Record<symbol, unknown>;
    const marcado = global[marca];
    delete global[marca];
    try {
      register();
      console.error(`falhou para ${EMAIL}`);
      expect(escrito).toEqual(["falhou para <REDACTED>"]);
    } finally {
      metodos.forEach((metodo, i) => { console[metodo] = originais[i] ?? console[metodo]; });
      if (marcado) global[marca] = marcado; else delete global[marca];
    }
  });
});
