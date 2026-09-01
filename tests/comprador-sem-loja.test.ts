import { describe, expect, it } from "vitest";
import { oauthCallbackHome } from "../lib/oauth-callback-home";

describe("para onde vai quem não tem loja", () => {
  it("leva à biblioteca quando a ausência de loja é comprovada", () => {
    // O caso que passou batido no #350: rodrigo.victor@hub2you.ai tem curso
    // comprado e nenhuma loja. Ele era recebido por "Seu negócio em um só
    // lugar", um botão "Criar produto" que daria erro, e três métricas
    // zeradas — e o menu o convidava a abrir uma loja como primeira ação.
    expect(oauthCallbackHome(null, false)).toBe("/library");
  });

  it("leva ao painel quem já tem loja", () => {
    expect(oauthCallbackHome(null, true)).toBe("/dashboard");
  });

  it("não reclassifica ninguém quando a listagem não respondeu", () => {
    // Um 503 momentâneo na listagem mandaria um vendedor existente para a
    // biblioteca e ele ficaria lá, mesmo com o menu já mostrando a loja.
    expect(oauthCallbackHome(null, null)).toBe("/dashboard");
  });

  it("mantém o destino da persona demo acima de tudo", () => {
    const demo = { home: "/platform/demo-inspection" } as never;
    expect(oauthCallbackHome(demo, false)).toBe("/platform/demo-inspection");
    expect(oauthCallbackHome(demo, true)).toBe("/platform/demo-inspection");
  });

  it("mantém o destino antigo quando ninguém informa", () => {
    expect(oauthCallbackHome(null)).toBe("/dashboard");
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(__dirname, "..");

describe("a biblioteca vazia não é um beco", () => {
  const biblioteca = readFileSync(resolve(root, "app/components/library-workspace.tsx"), "utf8");

  it("oferece criar loja a quem tem identidade real e nenhuma loja", () => {
    expect(biblioteca).toContain("showStoreInvite");
    expect(biblioteca).toContain("NEW_STORE_PATH");
  });

  it("não oferece à persona demo, à sessão não resolvida nem ao superadmin", () => {
    expect(biblioteca).toContain("!profile.profile");
    expect(biblioteca).toContain("profile.identity !== null");
    expect(biblioteca).toContain("platform_superadmin");
  });
});
