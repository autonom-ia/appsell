import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { readPublicStorefront } = vi.hoisted(() => ({ readPublicStorefront: vi.fn() }));
vi.mock("@/lib/internal-api-client.server", async (original) => {
  const real = await original<typeof import("@/lib/internal-api-client.server")>();
  return { ...real, readPublicStorefront };
});
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (nome: string) => (nome ? { value: "sessao-de-teste" } : undefined) })
}));
vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession: async () => ({ subject: "comprador" }) })
}));
vi.mock("@/lib/product-checkout-availability.server", () => ({ isProductCheckoutAvailable: () => true }));
vi.mock("@/lib/mutation-protection.server", async (original) => {
  const real = await original<typeof import("@/lib/mutation-protection.server")>();
  return { ...real, csrfTokenForSession: () => "csrf-de-teste" };
});

// A loja da Hub2You está no ar e não pode regredir (AGENTS.md §7). Sem o
// piloto, o HTML de cada página da loja tem de ser o mesmo da `main`: o teste
// pega a página como ela está no merge-base com a `main`, renderiza as duas
// versões com os mesmos dados e compara.
const RAIZ = fileURLToPath(new URL("..", import.meta.url));
const PASTA = join(RAIZ, "tests", ".linha-de-base-563");
const HUB2YOU = "11111111-1111-4111-8111-111111111111";

const PAGINAS = {
  vitrine: { caminho: "app/s/[sellerSlug]/page.tsx", params: { sellerSlug: "hub2you" } },
  produto: {
    caminho: "app/s/[sellerSlug]/p/[productSlug]/page.tsx",
    params: { sellerSlug: "hub2you", productSlug: "condicoes-gerais" }
  },
  acesso: {
    caminho: "app/s/[sellerSlug]/p/[productSlug]/access/page.tsx",
    params: { sellerSlug: "hub2you", productSlug: "material-gratuito" }
  }
} as const;

type Pagina = (props: { params: Promise<Record<string, string>> }) => Promise<ReactElement>;

function produto(id: string, slug: string, titulo: string, extra: Record<string, unknown>) {
  return {
    id,
    slug,
    title: titulo,
    description: "Produto.",
    publication_id: id.replace("2222", "3333"),
    publication_version: 1,
    ...extra
  };
}

function vitrine() {
  return {
    schema_version: 1,
    seller: { id: HUB2YOU, slug: "hub2you", name: "Hub2you", tagline: "IA para corretores" },
    products: [
      produto("22222222-2222-4222-8222-222222222221", "condicoes-gerais", "Condições gerais", {
        price: { unit_amount_minor: 4700, currency: "BRL", billing_period: "one_time" }
      }),
      produto("22222222-2222-4222-8222-222222222222", "gerador-de-cotacoes-comparativas", "Gerador de cotações comparativas", {
        price: { unit_amount_minor: 8700, currency: "BRL", billing_period: "one_time" }
      }),
      produto("22222222-2222-4222-8222-222222222223", "material-gratuito", "Material gratuito", {
        access_mode: "free",
        price: null
      })
    ]
  };
}

function baseDaMain(): string {
  for (const referencia of ["origin/main", "main"]) {
    try {
      return execFileSync("git", ["merge-base", "HEAD", referencia], { cwd: RAIZ }).toString().trim();
    } catch {
      // tenta a próxima referência
    }
  }
  throw new Error("sem merge-base com a main: o teste precisa do histórico do git");
}

async function paginaDaMain(caminho: string): Promise<Pagina> {
  const conteudo = execFileSync("git", ["show", `${baseDaMain()}:${caminho}`], { cwd: RAIZ });
  mkdirSync(PASTA, { recursive: true });
  const destino = join(PASTA, caminho.split("/").join("__"));
  writeFileSync(destino, conteudo);
  return (await import(destino)).default as Pagina;
}

async function paginaAtual(caminho: string): Promise<Pagina> {
  return (await import(join(RAIZ, caminho))).default as Pagina;
}

async function html(pagina: Pagina, params: Record<string, string>): Promise<string> {
  return renderToStaticMarkup(await pagina({ params: Promise.resolve(params) }));
}

afterAll(() => {
  rmSync(PASTA, { recursive: true, force: true });
});

describe("lojas fora do piloto de cookies ficam iguais à main", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    readPublicStorefront.mockResolvedValue(vitrine());
  });

  for (const [nome, { caminho, params }] of Object.entries(PAGINAS)) {
    it(`${nome}: sem piloto, o HTML da Hub2You é idêntico ao da main`, async () => {
      const daMain = await html(await paginaDaMain(caminho), params);
      const atual = await html(await paginaAtual(caminho), params);
      expect(atual).toBe(daMain);
      vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", "99999999-9999-4999-8999-999999999999");
      expect(await html(await paginaAtual(caminho), params)).toBe(daMain);
      expect(atual).not.toContain(">Cookies</button>");
    });
  }

  it("a vitrine mantém o que o gate da Hub2You confere", async () => {
    const atual = await html(await paginaAtual(PAGINAS.vitrine.caminho), PAGINAS.vitrine.params);
    expect(atual.split("Ver e comprar").length - 1).toBe(2);
    expect(atual).toContain("R$ 47,00");
    expect(atual).toContain("R$ 87,00");
  });

  it("com a loja no piloto, só acrescenta o botão Cookies depois do rodapé", async () => {
    const { caminho, params } = PAGINAS.vitrine;
    const daMain = await html(await paginaDaMain(caminho), params);
    vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", HUB2YOU);
    const comPiloto = await html(await paginaAtual(caminho), params);
    expect(comPiloto).toContain(">Cookies</button>");
    expect(comPiloto).not.toContain("sell-cookies-titulo");
    const fimDoRodape = daMain.lastIndexOf("</footer>") + "</footer>".length;
    expect(comPiloto.startsWith(daMain.slice(0, fimDoRodape))).toBe(true);
    expect(comPiloto.endsWith(daMain.slice(fimDoRodape))).toBe(true);
  });
});
