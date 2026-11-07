import { describe, expect, it } from "vitest";

import { IP_DESCONHECIDO, ipDoCliente } from "../lib/ip-do-cliente";

function pedido(cabecalhos: Record<string, string>) {
  return { headers: new Headers(cabecalhos) };
}

describe("ipDoCliente", () => {
  it("usa o endereço que a borda acrescentou, não o que o pedido trouxe", () => {
    // O pedido chega com um X-Forwarded-For escrito por ele; a borda põe o
    // endereço da conexão no fim.
    expect(ipDoCliente(pedido({ "x-forwarded-for": "203.0.113.7, 198.51.100.20" }))).toBe(
      "198.51.100.20"
    );
  });

  it("trocar o cabeçalho a cada pedido não muda o endereço", () => {
    const enderecos = ["1.1.1.1", "2.2.2.2", "qualquer-coisa"].map((forjado) =>
      ipDoCliente(pedido({ "x-forwarded-for": `${forjado}, 198.51.100.20` }))
    );
    expect(new Set(enderecos)).toEqual(new Set(["198.51.100.20"]));
  });

  it("com um item só, é ele", () => {
    expect(ipDoCliente(pedido({ "x-forwarded-for": "198.51.100.20" }))).toBe("198.51.100.20");
  });

  it("aceita IPv6 e espaços", () => {
    expect(ipDoCliente(pedido({ "x-forwarded-for": " 203.0.113.7 ,  2001:db8::1 " }))).toBe(
      "2001:db8::1"
    );
  });

  it("ignora item vazio no fim", () => {
    expect(ipDoCliente(pedido({ "x-forwarded-for": "198.51.100.20, " }))).toBe("198.51.100.20");
  });

  it("o X-Real-IP do pedido não serve de reserva", () => {
    expect(ipDoCliente(pedido({ "x-real-ip": "198.51.100.30" }))).toBe(IP_DESCONHECIDO);
  });

  it("X-Forwarded-For vazio é o marcador fixo", () => {
    expect(ipDoCliente(pedido({ "x-forwarded-for": " , ", "x-real-ip": "198.51.100.30" }))).toBe(
      IP_DESCONHECIDO
    );
  });

  it("sem nenhum dos dois, devolve o marcador fixo", () => {
    expect(ipDoCliente(pedido({}))).toBe(IP_DESCONHECIDO);
  });
});
