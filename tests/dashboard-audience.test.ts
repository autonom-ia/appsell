import { describe, expect, it } from "vitest";
import { dashboardHeader } from "../lib/dashboard-audience";

describe("o que a Visão geral pode afirmar", () => {
  it("oferece criar produto a quem tem loja", () => {
    expect(dashboardHeader("real", true)).toEqual({ activation: false, cta: true });
  });

  it("troca a promessa de negócio pela ativação quando a ausência de loja é comprovada", () => {
    // O comprador com curso e sem loja, que era recebido por "Seu negócio em
    // um só lugar" e um botão que daria erro.
    expect(dashboardHeader("real", false)).toEqual({ activation: true, cta: false });
  });

  it("não afirma ausência de loja enquanto não sabe", () => {
    // Vendedor existente com a sessão lenta veria "Comece criando sua loja"
    // e depois o painel mudaria debaixo dele.
    expect(dashboardHeader("real", null)).toEqual({ activation: false, cta: false });
    expect(dashboardHeader("loading", null)).toEqual({ activation: false, cta: false });
    expect(dashboardHeader("failed", null)).toEqual({ activation: false, cta: false });
  });

  it("preserva o demo, que demonstra a jornada inteira do vendedor", () => {
    // As personas demo não têm identidade resolvida, então hasStore é null.
    // Sem este caso, o demo de admin perdia o botão Criar produto.
    expect(dashboardHeader("demo", null)).toEqual({ activation: false, cta: true });
  });
});
