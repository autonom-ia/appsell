import { describe, expect, it } from "vitest";
import {
  hasPlatformGrant,
  shellAudience,
  type ProfileState,
  type ShellIdentity
} from "../app/components/shell-profile-context";

function identity(partial: Partial<ShellIdentity> = {}): ShellIdentity {
  return {
    email: "pessoa@exemplo.test",
    emailVerified: true,
    platformRoles: [],
    sellers: [],
    ...partial
  };
}

function ready(over: Partial<Extract<ProfileState, { status: "ready" }>> = {}): ProfileState {
  return { status: "ready", profile: null, identity: identity(), runtime: "identity", ...over };
}

describe("audiência do shell", () => {
  it("classifica como comprador quem não tem membership nem grant", () => {
    // O caso da #305: entitlement ativo, zero membership, zero grant.
    expect(shellAudience(ready())).toBe("without-store");
    expect(hasPlatformGrant(ready())).toBe(false);
  });

  it("classifica como vendedor quem tem pelo menos uma membership", () => {
    const state = ready({
      identity: identity({
        sellers: [{ id: "s1", slug: "hub2you", displayName: "Hub2you", role: "seller_owner" }]
      })
    });
    expect(shellAudience(state)).toBe("seller");
  });

  it("reconhece o grant de plataforma sem confundir com membership", () => {
    const state = ready({ identity: identity({ platformRoles: ["platform_superadmin"] }) });
    expect(hasPlatformGrant(state)).toBe(true);
    // Grant global não cria membership falsa: a matriz é explícita nisso.
    expect(shellAudience(state)).toBe("without-store");
  });

  it("ignora papéis de plataforma que não são superadmin", () => {
    const state = ready({ identity: identity({ platformRoles: ["platform_auditor"] }) });
    expect(hasPlatformGrant(state)).toBe(false);
  });

  it("não decide audiência enquanto o perfil carrega", () => {
    // Se decidisse, o menu do vendedor apareceria e depois encolheria — o item
    // que some debaixo do cursor é pior do que o item que aparece depois.
    expect(shellAudience({ status: "loading" })).toBeNull();
  });

  it("não decide audiência quando o servidor não resolveu o papel", () => {
    // Falha fechada: sem identidade resolvida não afirmamos nada. Assumir
    // "vendedor" mostraria Recebimentos a um aluno; assumir "comprador"
    // esconderia a criação da loja de quem acabou de se cadastrar para vender.
    expect(shellAudience(ready({ identity: null }))).toBeNull();
    expect(shellAudience({ status: "failed" })).toBeNull();
  });

  it("mantém a persona demo decidindo no runtime local", () => {
    const demo = (persona: "admin" | "buyer" | "superadmin") => ready({
      runtime: "local",
      identity: null,
      profile: { persona, email: `${persona}@demo.test`, label: persona, home: "/dashboard" }
    });
    expect(shellAudience(demo("buyer"))).toBe("demo-buyer");
    expect(shellAudience(demo("admin"))).toBe("seller");
    expect(hasPlatformGrant(demo("superadmin"))).toBe(true);
  });
});

describe("superadmin real", () => {
  it("não é convidado a abrir loja, e não recebe o menu do vendedor", () => {
    // O grant global não cria membership — a matriz é explícita. Ele cai em
    // "sem loja", mas convidá-lo a criar uma leria mal o que ele veio fazer.
    // O acesso à área de plataforma em si é entrega da onda A4: hoje a rota é
    // travada em local/CI e um link no menu seria um botão para o 403.
    const state = ready({ identity: identity({ platformRoles: ["platform_superadmin"] }) });
    expect(shellAudience(state)).toBe("without-store");
    expect(hasPlatformGrant(state)).toBe(true);
  });
});
