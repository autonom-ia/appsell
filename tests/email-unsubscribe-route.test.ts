import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const { descadastrarPorToken, DescadastroIndisponivel } = vi.hoisted(() => {
  class DescadastroIndisponivel extends Error {}
  return { descadastrarPorToken: vi.fn(), DescadastroIndisponivel };
});

vi.mock("../app/api/email/unsubscribe/descadastro.server", () => ({
  descadastrarPorToken,
  DescadastroIndisponivel
}));

import { POST } from "../app/api/email/unsubscribe/route";
import { limparLimiteParaTeste } from "../app/api/email/unsubscribe/limite";

const URL_BASE = "https://sell.test/api/email/unsubscribe";
const TOKEN = "a".repeat(43);

function pedido(
  corpo: string,
  { tipo = "application/json", query = "", ip = "203.0.113.7" } = {}
) {
  return new NextRequest(`${URL_BASE}${query}`, {
    method: "POST",
    body: corpo,
    headers: { "content-type": tipo, "x-forwarded-for": ip }
  });
}

function cabecalhosSeguros(resposta: Response) {
  expect(resposta.headers.get("cache-control")).toBe("no-store");
  expect(resposta.headers.get("referrer-policy")).toBe("no-referrer");
  expect(resposta.headers.get("x-robots-tag")).toBe("noindex");
}

describe("POST /api/email/unsubscribe", () => {
  beforeEach(() => {
    limparLimiteParaTeste();
    descadastrarPorToken.mockReset();
    descadastrarPorToken.mockResolvedValue(undefined);
  });

  it("descadastra pelo token no corpo, que é o que a página manda", async () => {
    const resposta = await POST(pedido(JSON.stringify({ token: TOKEN })));
    expect(resposta.status).toBe(200);
    expect(descadastrarPorToken).toHaveBeenCalledWith(TOKEN);
    cabecalhosSeguros(resposta);
  });

  it("aceita o one-click do RFC 8058: token na query e corpo List-Unsubscribe=One-Click", async () => {
    const resposta = await POST(
      pedido("List-Unsubscribe=One-Click", {
        tipo: "application/x-www-form-urlencoded",
        query: `?t=${TOKEN}`
      })
    );
    expect(resposta.status).toBe(200);
    expect(descadastrarPorToken).toHaveBeenCalledWith(TOKEN);
  });

  it("responde igual quando não há token, e não chama a API", async () => {
    const vazio = await POST(pedido("{}"));
    const invalido = await POST(pedido("nao-e-json"));
    const valido = await POST(pedido(JSON.stringify({ token: TOKEN })));
    expect(vazio.status).toBe(200);
    expect(invalido.status).toBe(200);
    expect(await vazio.text()).toBe(await valido.text());
    expect(descadastrarPorToken).toHaveBeenCalledTimes(1);
  });

  it("recusa corpo acima de 1 KB", async () => {
    const resposta = await POST(pedido(JSON.stringify({ token: "x".repeat(2000) })));
    expect(resposta.status).toBe(413);
    expect(descadastrarPorToken).not.toHaveBeenCalled();
  });

  it("recusa corpo grande mesmo sem content-length (chunked)", async () => {
    const enorme = new TextEncoder().encode(JSON.stringify({ token: "x".repeat(4000) }));
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (let i = 0; i < enorme.length; i += 256) controller.enqueue(enorme.slice(i, i + 256));
        controller.close();
      }
    });
    // `duplex` é exigido pelo undici para corpo em stream, mas não está no tipo.
    const init = {
      method: "POST",
      body: stream,
      headers: { "content-type": "application/json", "x-forwarded-for": "192.0.2.9" },
      duplex: "half"
    } as unknown as ConstructorParameters<typeof NextRequest>[1];
    const pedidoSemTamanho = new NextRequest(URL_BASE, init);
    expect(pedidoSemTamanho.headers.get("content-length")).toBeNull();
    const resposta = await POST(pedidoSemTamanho);
    expect(resposta.status).toBe(413);
    expect(descadastrarPorToken).not.toHaveBeenCalled();
  });

  it("limita a taxa por IP", async () => {
    const respostas = [];
    for (let i = 0; i < 12; i += 1) {
      respostas.push(await POST(pedido(JSON.stringify({ token: TOKEN }))));
    }
    expect(respostas.slice(0, 10).every((r) => r.status === 200)).toBe(true);
    expect(respostas[10].status).toBe(429);
    expect(respostas[11].status).toBe(429);
    const outroIp = await POST(pedido(JSON.stringify({ token: TOKEN }), { ip: "198.51.100.2" }));
    expect(outroIp.status).toBe(200);
  });

  it("API fora responde 503 sem ecoar o token", async () => {
    descadastrarPorToken.mockRejectedValue(new DescadastroIndisponivel());
    const resposta = await POST(pedido(JSON.stringify({ token: TOKEN })));
    expect(resposta.status).toBe(503);
    expect(await resposta.text()).not.toContain(TOKEN);
    cabecalhosSeguros(resposta);
  });
});
