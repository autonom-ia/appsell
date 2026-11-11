import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

// A página de checkout real (#154, achados 2 e 3) decide pela mesma leitura de
// sessão da rota de cobrança: quem vai ao login, quem vê um aviso e quem vê o
// card de pagamento. Estes testes renderizam a página de verdade.

vi.mock("server-only", () => ({}));

const { getSession, readPublicStorefront, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return { getSession: vi.fn(), readPublicStorefront: vi.fn(), DomainApiError };
});

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => ({ value: "sessao-de-teste" }) })
}));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`REDIRECT ${path}`);
  }
}));
vi.mock("@/lib/oauth-service.server", () => ({ oauthService: () => ({ getSession }) }));
vi.mock("@/lib/internal-api-client.server", () => ({ readPublicStorefront, DomainApiError }));
vi.mock("@/lib/product-checkout-availability.server", () => ({ isProductCheckoutAvailable: () => true }));
vi.mock("../app/components/stripe-checkout-card", () => ({
  StripeCheckoutCard: (props: { accountEmail: string; priceLabel: string }) =>
    `CARD ${props.accountEmail} ${props.priceLabel}`
}));
vi.mock("../app/components/demo-checkout-workspace", () => ({ DemoCheckoutWorkspace: () => "DEMO" }));

import DemoCheckoutPage from "../app/(protected)/checkout/page";

const SELLER = "loja";
const PRODUCT = "curso";

function storefront() {
  return {
    schema_version: 1,
    seller: { id: "11111111-1111-4111-8111-111111111111", slug: SELLER, name: "Loja Teste" },
    products: [{
      id: "22222222-2222-4222-8222-222222222222",
      slug: PRODUCT,
      title: "Curso Teste",
      description: "Curso",
      publication_id: "33333333-3333-4333-8333-333333333333",
      publication_version: 1,
      price: { unit_amount_minor: 4_700, currency: "BRL", billing_period: "one_time" }
    }]
  };
}

function session(email: string, emailVerified = true, subject = "sub-1") {
  return { subject, identity: { email, emailVerified } };
}

async function render(): Promise<string> {
  const element = await DemoCheckoutPage({
    searchParams: Promise.resolve({ real: "1", seller: SELLER, product: PRODUCT })
  });
  return renderToStaticMarkup(element as ReactElement);
}

describe("página de checkout real", () => {
  beforeEach(() => {
    readPublicStorefront.mockResolvedValue(storefront());
  });

  it("comprador verificado vê o card com o e-mail da conta e o preço", async () => {
    getSession.mockResolvedValueOnce(session("Buyer@Example.test"));
    const html = await render();
    expect(html).toContain("CARD buyer@example.test R$ 47,00");
  });

  it("sessão vencida, subject de registration: e e-mail não verificado vão ao login", async () => {
    getSession.mockResolvedValueOnce(null);
    await expect(render()).rejects.toThrow("REDIRECT /auth/login");
    getSession.mockResolvedValueOnce(session("buyer@example.test", true, "registration:buyer@example.test"));
    await expect(render()).rejects.toThrow("REDIRECT /auth/login");
    getSession.mockResolvedValueOnce(session("buyer@example.test", false));
    await expect(render()).rejects.toThrow("REDIRECT /auth/login");
  });

  it("session store fora do ar mostra aviso, sem card e sem ler a loja", async () => {
    getSession.mockImplementationOnce(async () => {
      throw new Error("redis down");
    });
    readPublicStorefront.mockClear();
    const html = await render();
    expect(html).toContain("Não foi possível confirmar seu login");
    expect(html).not.toContain("CARD");
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });

  it("e-mail verificado que o pagamento recusa mostra aviso, sem card", async () => {
    getSession.mockResolvedValueOnce(session("buyer@localhost"));
    const html = await render();
    expect(html).toContain("Este e-mail não pode ser usado no pagamento");
    expect(html).not.toContain("CARD");
  });

  it("loja fora do ar e produto fora de venda têm avisos diferentes", async () => {
    getSession.mockResolvedValueOnce(session("buyer@example.test"));
    readPublicStorefront.mockRejectedValueOnce(new DomainApiError(503, "unavailable"));
    const fora = await render();
    expect(fora).toContain("Loja fora do ar");
    expect(fora).not.toContain("CARD");

    getSession.mockResolvedValueOnce(session("buyer@example.test"));
    readPublicStorefront.mockRejectedValueOnce(new DomainApiError(404, "not_found"));
    const semVenda = await render();
    expect(semVenda).toContain("Compra indisponível");
    expect(semVenda).not.toContain("Loja fora do ar");
  });
});
