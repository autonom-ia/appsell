import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// As duas rotas que usam o endereço do cliente: o descadastro (limite de taxa)
// e o contato comercial (endereço no e-mail). As duas leem `ipDoCliente`.
vi.mock("server-only", () => ({}));

const { descadastrarPorToken, enviarContatoComercial } = vi.hoisted(() => ({
  descadastrarPorToken: vi.fn(),
  enviarContatoComercial: vi.fn()
}));

vi.mock("../app/api/email/unsubscribe/descadastro.server", () => ({
  descadastrarPorToken,
  DescadastroIndisponivel: class extends Error {}
}));
vi.mock("@/lib/email/contato-comercial", () => ({ enviarContatoComercial }));

import { POST as descadastrar } from "../app/api/email/unsubscribe/route";
import { limparLimiteParaTeste } from "../app/api/email/unsubscribe/limite";
import { POST as contatar } from "../app/api/contato/route";

const DA_BORDA = "198.51.100.20";
const TOKEN = "a".repeat(43);

function comEndereco(url: string, corpo: string, encaminhado: string) {
  return new NextRequest(url, {
    method: "POST",
    body: corpo,
    headers: { "content-type": "application/json", "x-forwarded-for": encaminhado }
  });
}

describe("o endereço do cliente nas rotas", () => {
  beforeEach(() => {
    limparLimiteParaTeste();
    descadastrarPorToken.mockReset();
    descadastrarPorToken.mockResolvedValue(undefined);
    enviarContatoComercial.mockReset();
    enviarContatoComercial.mockResolvedValue(true);
  });

  it("descadastro: o limite conta pelo endereço da borda, mesmo trocando o começo do cabeçalho", async () => {
    const status = [];
    for (let i = 0; i < 12; i += 1) {
      const resposta = await descadastrar(
        comEndereco(
          "https://sell.test/api/email/unsubscribe",
          JSON.stringify({ token: TOKEN }),
          `203.0.113.${i}, ${DA_BORDA}`
        )
      );
      status.push(resposta.status);
    }
    expect(status.slice(0, 10).every((codigo) => codigo === 200)).toBe(true);
    expect(status.slice(10)).toEqual([429, 429]);
  });

  it("descadastro: outro endereço da borda tem o próprio limite", async () => {
    for (let i = 0; i < 11; i += 1) {
      await descadastrar(
        comEndereco("https://sell.test/api/email/unsubscribe", "{}", `203.0.113.9, ${DA_BORDA}`)
      );
    }
    const outro = await descadastrar(
      comEndereco("https://sell.test/api/email/unsubscribe", "{}", "203.0.113.9, 198.51.100.21")
    );
    expect(outro.status).toBe(200);
  });

  it("contato: o e-mail leva o endereço da borda, não o primeiro item", async () => {
    const resposta = await contatar(
      comEndereco(
        "https://sell.test/api/contato",
        JSON.stringify({
          nome: "Pessoa Teste",
          email: "pessoa@example.test",
          loja: "Loja Teste",
          mensagem: "Quero entender como vender cursos por aqui."
        }),
        `203.0.113.7, ${DA_BORDA}`
      )
    );
    expect(resposta.status).toBe(200);
    expect(enviarContatoComercial).toHaveBeenCalledWith(expect.objectContaining({ ip: DA_BORDA }));
  });
});
