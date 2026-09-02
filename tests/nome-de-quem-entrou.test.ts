import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const readDomainIdentity = vi.fn();
const listDomainSellers = vi.fn();
vi.mock("@/lib/internal-api-client.server", () => ({
  readDomainIdentity: (...a: unknown[]) => readDomainIdentity(...a),
  listDomainSellers: (...a: unknown[]) => listDomainSellers(...a),
  isDomainApiConfigured: () => true
}));

const { resolveSessionIdentity } = await import("../lib/session-identity.server");
const call = () => resolveSessionIdentity("s", { subject: "x" } as never);

function me(over: Record<string, unknown> = {}) {
  return { id: "u", email: "p@exemplo.test", email_verified: true, platform_roles: [], ...over };
}

describe("o nome que o Identity conhece", () => {
  it("chega até a sessão quando a API o devolve", async () => {
    readDomainIdentity.mockResolvedValueOnce(me({ display_name: "Rodrigo Victor" }));
    listDomainSellers.mockResolvedValueOnce({ sellers: [] });
    expect((await call())?.displayName).toBe("Rodrigo Victor");
  });

  it("vira ausência quando não veio, em vez de string vazia", async () => {
    // O rodapé mostraria um nome em branco no lugar do rótulo neutro.
    for (const valor of [undefined, null, "", "   ", 42]) {
      readDomainIdentity.mockResolvedValueOnce(me({ display_name: valor }));
      listDomainSellers.mockResolvedValueOnce({ sellers: [] });
      expect((await call())?.displayName).toBeNull();
    }
  });

  it("não impede a resolução da identidade quando o nome falta", async () => {
    // O nome é acessório: sem ele a sessão continua válida e o papel resolve.
    readDomainIdentity.mockResolvedValueOnce(me());
    listDomainSellers.mockResolvedValueOnce({ sellers: [] });
    const resolved = await call();
    expect(resolved).not.toBeNull();
    expect(resolved?.email).toBe("p@exemplo.test");
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("a leitura do claim no ID token", () => {
  const fonte = readFileSync(resolve(__dirname, "../lib/identity-oauth-client.server.ts"), "utf8");

  it("prefere o nome como a pessoa o escreveu, e só então junta as partes", () => {
    expect(fonte).toContain("claims.name");
    expect(fonte).toContain("claims.given_name");
    expect(fonte).toContain("claims.family_name");
  });

  it("nunca lança por causa do nome", () => {
    // canonicalEmail lança; usableName devolve undefined. Recusar a sessão por
    // causa de um rótulo trocaria um nome bonito por uma porta fechada.
    const trecho = fonte.slice(fonte.indexOf("function usableName"), fonte.indexOf("function canonicalEmail"));
    expect(trecho).not.toContain("throw");
    expect(trecho).toContain("return undefined");
  });
});

describe("o nome não entra no cofre de tokens", () => {
  const cofre = readFileSync(resolve(__dirname, "../lib/token-vault.ts"), "utf8");

  it("mantém a lista exata de chaves da identidade selada", () => {
    // O cofre valida por allowlist. Abrir essa fronteira para exibir um rótulo
    // arriscaria as sessões ativas; o nome viaja até o banco por outro caminho.
    expect(cofre).toContain('["issuer", "email", "emailVerified"]');
    expect(cofre).not.toContain("displayName");
  });
});
