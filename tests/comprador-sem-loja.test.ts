import { describe, expect, it } from "vitest";
import { oauthCallbackHome } from "../lib/oauth-callback-home";

describe("para onde vai quem não tem loja", () => {
  it("leva à biblioteca, não ao painel do vendedor", () => {
    // O caso que passou batido no #350: rodrigo.victor@hub2you.ai tem curso
    // comprado e nenhuma loja. Ele era recebido por "Seu negócio em um só
    // lugar", um botão "Criar produto" que daria erro, e três métricas
    // zeradas — e o menu o convidava a abrir uma loja como primeira ação.
    expect(oauthCallbackHome(null, false)).toBe("/library");
  });

  it("leva ao painel quem já tem loja", () => {
    expect(oauthCallbackHome(null, true)).toBe("/dashboard");
  });

  it("mantém o destino da persona demo acima de tudo", () => {
    const demo = { home: "/platform/demo-inspection" } as never;
    expect(oauthCallbackHome(demo, false)).toBe("/platform/demo-inspection");
    expect(oauthCallbackHome(demo, true)).toBe("/platform/demo-inspection");
  });

  it("assume a ausência de loja quando ninguém informa", () => {
    // Falha fechada também aqui: sem saber, não se promete um negócio.
    expect(oauthCallbackHome(null)).toBe("/library");
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(__dirname, "..");

describe("o painel do vendedor não fala com quem não tem loja", () => {
  const painel = readFileSync(resolve(root, "app/components/dashboard-overview.tsx"), "utf8");

  it("só oferece Criar produto quando existe loja", () => {
    // Sem loja não há catálogo onde criar: o botão levava a um erro.
    expect(painel).toContain("const showCta = hasStore &&");
  });

  it("troca a promessa de negócio pela ativação quando não há loja", () => {
    expect(painel).toContain("Comece criando sua loja.");
    expect(painel).toContain("hasStore || view === \"demo\"");
  });

  it("só mostra métricas quando há loja e há produto", () => {
    // A condição anterior era !(hasSeller && total === 0): com hasSeller
    // falso ela virava verdadeira, e as três métricas zeradas apareciam
    // justamente para quem não tinha nada.
    expect(painel).toContain("ready && hasSeller && metrics.total > 0");
    expect(painel).not.toContain("!(hasSeller && metrics.total === 0)");
  });
});

describe("a biblioteca vazia não é um beco", () => {
  const biblioteca = readFileSync(resolve(root, "app/components/library-workspace.tsx"), "utf8");

  it("oferece criar loja a quem tem identidade real e nenhuma loja", () => {
    expect(biblioteca).toContain("showStoreInvite");
    expect(biblioteca).toContain("NEW_STORE_PATH");
  });

  it("não oferece a persona demo nem a sessão não resolvida", () => {
    expect(biblioteca).toContain("!profile.profile");
    expect(biblioteca).toContain("profile.identity !== null");
  });
});
