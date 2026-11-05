import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { readPublicStorefront } = vi.hoisted(() => ({ readPublicStorefront: vi.fn() }));
vi.mock("@/lib/internal-api-client.server", async (original) => {
  const real = await original<typeof import("@/lib/internal-api-client.server")>();
  return { ...real, readPublicStorefront };
});

import SellerStorefront from "../app/s/[sellerSlug]/page";

// A loja da Hub2You está no ar e não pode regredir (AGENTS.md §7). Sem o
// piloto, o HTML da vitrine dela tem de ser o mesmo de antes da #563.
const HUB2YOU = "11111111-1111-4111-8111-111111111111";

function vitrine() {
  return {
    schema_version: 1,
    seller: { id: HUB2YOU, slug: "hub2you", name: "Hub2you", tagline: "IA para corretores" },
    products: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        slug: "condicoes-gerais",
        title: "Condições gerais",
        description: "Produto.",
        publication_id: "33333333-3333-4333-8333-333333333333",
        publication_version: 1,
        price: { unit_amount_minor: 4700, currency: "BRL", billing_period: "one_time" }
      },
      {
        id: "44444444-4444-4444-8444-444444444444",
        slug: "gerador-de-cotacoes-comparativas",
        title: "Gerador de cotações comparativas",
        description: "Produto.",
        publication_id: "55555555-5555-4555-8555-555555555555",
        publication_version: 1,
        price: { unit_amount_minor: 8700, currency: "BRL", billing_period: "one_time" }
      }
    ]
  };
}

async function html(): Promise<string> {
  const pagina = await SellerStorefront({ params: Promise.resolve({ sellerSlug: "hub2you" }) });
  return renderToStaticMarkup(pagina as ReactElement);
}

describe("vitrine da Hub2You e o piloto de cookies", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    readPublicStorefront.mockResolvedValue(vitrine());
  });

  it("sem piloto, o HTML é idêntico ao da vitrine sem a #563", async () => {
    const semVariavel = await html();
    vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", "99999999-9999-4999-8999-999999999999");
    const comOutraLoja = await html();
    expect(comOutraLoja).toBe(semVariavel);
    expect(semVariavel).not.toContain("Cookies");
    expect(semVariavel).not.toContain("sell-cookies");
    // O que o gate da Hub2You confere continua lá.
    expect(semVariavel.split("Ver e comprar").length - 1).toBe(2);
    expect(semVariavel).toContain("R$ 47,00");
    expect(semVariavel).toContain("R$ 87,00");
  });

  it("com a loja no piloto, só acrescenta o link Cookies depois do rodapé", async () => {
    const antes = await html();
    vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", HUB2YOU);
    const depois = await html();
    expect(depois).toContain(">Cookies</button>");
    // O banner não vem no HTML do servidor: só aparece no navegador, sem escolha.
    expect(depois).not.toContain("sell-cookies-titulo");
    const fimDoRodape = antes.lastIndexOf("</footer>") + "</footer>".length;
    expect(depois.startsWith(antes.slice(0, fimDoRodape))).toBe(true);
  });
});
