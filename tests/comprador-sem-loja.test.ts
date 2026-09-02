import { describe, expect, it } from "vitest";
import { oauthCallbackHome } from "../lib/oauth-callback-home";
import { canOpenFirstStore, type ShellIdentity } from "../app/components/shell-profile-context";

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

describe("quem pode abrir a primeira loja", () => {
  const base = { status: "ready", profile: null, runtime: "identity" } as const;
  const identidade = (over: Partial<ShellIdentity> = {}): ShellIdentity => ({
    email: "p@exemplo.test", displayName: null, emailVerified: true, platformRoles: [], sellers: [], ...over
  });

  it("oferece a quem tem identidade real, nenhuma loja e nenhum papel de plataforma", () => {
    expect(canOpenFirstStore({ ...base, identity: identidade() })).toBe(true);
  });

  it("não oferece a quem já tem loja", () => {
    const state = { ...base, identity: identidade({
      sellers: [{ id: "s", slug: "l", displayName: "L", role: "seller_owner" }]
    }) };
    expect(canOpenFirstStore(state)).toBe(false);
  });

  it("não oferece a nenhum papel de plataforma, não só ao superadmin", () => {
    // support e auditor também operam a plataforma; a matriz descreve criar
    // tenant próprio como fluxo de quem ainda não tem papel nenhum.
    for (const role of ["platform_superadmin", "platform_support", "platform_auditor"]) {
      expect(canOpenFirstStore({ ...base, identity: identidade({ platformRoles: [role] }) }))
        .toBe(false);
    }
  });

  it("não oferece a persona demo nem a sessão não resolvida", () => {
    expect(canOpenFirstStore({ ...base, identity: null })).toBe(false);
    expect(canOpenFirstStore({ status: "loading" })).toBe(false);
    expect(canOpenFirstStore({ status: "failed" })).toBe(false);
    expect(canOpenFirstStore({
      ...base, identity: null,
      profile: { persona: "buyer", email: "b@d.test", label: "Aluno", home: "/library" }
    })).toBe(false);
  });
});
