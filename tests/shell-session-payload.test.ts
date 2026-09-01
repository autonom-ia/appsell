import { describe, expect, it } from "vitest";
import { parseProfileState } from "../app/components/app-shell";
import { shellAudience } from "../app/components/shell-profile-context";

const base = { authenticated: true, expiresAt: 1, runtime: "identity", demoProfile: null };

describe("leitura do payload de sessão no shell", () => {
  it("lê identidade real e deriva a audiência a partir dela", () => {
    const state = parseProfileState({
      ...base,
      identity: {
        email: "vendedor@exemplo.test",
        emailVerified: true,
        platformRoles: [],
        sellers: [{ id: "s1", slug: "loja", displayName: "Loja", role: "seller_owner" }]
      }
    });
    expect(state.status).toBe("ready");
    expect(shellAudience(state)).toBe("seller");
  });

  it("aceita identidade ausente como papel não resolvido, não como comprador", () => {
    // identity null é "o servidor não conseguiu dizer", e não "não tem loja".
    const state = parseProfileState({ ...base, identity: null });
    expect(state.status).toBe("ready");
    expect(shellAudience(state)).toBeNull();
  });

  it("recusa a sessão inteira quando a identidade vem malformada", () => {
    const cases: unknown[] = [
      { ...base, identity: { email: "", platformRoles: [], sellers: [] } },
      { ...base, identity: { email: "a@b.test", platformRoles: "owner", sellers: [] } },
      { ...base, identity: { email: "a@b.test", platformRoles: [], sellers: "nenhuma" } },
      { ...base, identity: { email: "a@b.test", platformRoles: [], sellers: [{ id: 1 }] } },
      { ...base, identity: [] }
    ];
    for (const payload of cases) {
      expect(parseProfileState(payload).status).toBe("failed");
    }
  });

  it("recusa sessão não autenticada", () => {
    expect(parseProfileState({ authenticated: false }).status).toBe("failed");
    expect(parseProfileState(null).status).toBe("failed");
  });

  it("mantém o contrato demo intacto", () => {
    const state = parseProfileState({
      ...base,
      runtime: "local",
      demoProfile: {
        persona: "buyer",
        email: "user.demo@autonomia.site",
        label: "Aluno",
        home: "/library"
      }
    });
    expect(state.status).toBe("ready");
    expect(shellAudience(state)).toBe("demo-buyer");
  });
});
