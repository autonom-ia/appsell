import { describe, expect, it, vi } from "vitest";


vi.mock("server-only", () => ({}));
const readDomainIdentity = vi.fn();
const listDomainSellers = vi.fn();
const isDomainApiConfigured = vi.fn(() => true);

vi.mock("@/lib/internal-api-client.server", () => ({
  readDomainIdentity: (...args: unknown[]) => readDomainIdentity(...args),
  listDomainSellers: (...args: unknown[]) => listDomainSellers(...args),
  isDomainApiConfigured: () => isDomainApiConfigured()
}));

const { resolveSessionIdentity } = await import("../lib/session-identity.server");

const session = { subject: "sub-1", createdAt: 0, expiresAt: 0, tokenRevision: 1 };
const call = () => resolveSessionIdentity("sess-1", session as never);

function identityPayload(roles: string[] = []) {
  return { id: "u1", email: "pessoa@exemplo.test", email_verified: true, platform_roles: roles };
}

describe("resolução do papel no servidor", () => {
  it("devolve papel real a partir das duas fontes de verdade", async () => {
    readDomainIdentity.mockResolvedValueOnce(identityPayload(["platform_superadmin"]));
    listDomainSellers.mockResolvedValueOnce({
      sellers: [{ id: "s1", slug: "hub2you", display_name: "Hub2you", role: "seller_owner" }]
    });
    const resolved = await call();
    expect(resolved).toEqual({
      email: "pessoa@exemplo.test",
      emailVerified: true,
      platformRoles: ["platform_superadmin"],
      sellers: [{ id: "s1", slug: "hub2you", displayName: "Hub2you", role: "seller_owner" }]
    });
  });

  it("devolve lista vazia para identidade sem nenhuma loja", async () => {
    readDomainIdentity.mockResolvedValueOnce(identityPayload());
    listDomainSellers.mockResolvedValueOnce({ sellers: [] });
    expect((await call())?.sellers).toEqual([]);
  });

  it("recusa payload de loja incompleto em vez de completar com o que falta", async () => {
    // Um seller sem role viraria "vendedor de papel desconhecido". Preferimos
    // não resolver: o shell trata como sessão não validada.
    readDomainIdentity.mockResolvedValueOnce(identityPayload());
    listDomainSellers.mockResolvedValueOnce({ sellers: [{ id: "s1", slug: "x", display_name: "X" }] });
    expect(await call()).toBeNull();
  });

  it("recusa papéis de plataforma que não sejam texto", async () => {
    readDomainIdentity.mockResolvedValueOnce({ ...identityPayload(), platform_roles: [42] });
    listDomainSellers.mockResolvedValueOnce({ sellers: [] });
    expect(await call()).toBeNull();
  });

  it("não consulta a API de domínio quando ela não está configurada", async () => {
    isDomainApiConfigured.mockReturnValueOnce(false);
    readDomainIdentity.mockClear();
    listDomainSellers.mockClear();
    expect(await call()).toBeNull();
    expect(readDomainIdentity).not.toHaveBeenCalled();
    expect(listDomainSellers).not.toHaveBeenCalled();
  });

  it("propaga a falha para o chamador em vez de inventar um papel", async () => {
    readDomainIdentity.mockRejectedValueOnce(new Error("domain_api_unavailable"));
    listDomainSellers.mockResolvedValueOnce({ sellers: [] });
    await expect(call()).rejects.toThrow();
  });
});

describe("validação semântica dos papéis", () => {
  it("recusa membership com papel que o banco não reconhece", async () => {
    readDomainIdentity.mockResolvedValueOnce(identityPayload());
    listDomainSellers.mockResolvedValueOnce({
      sellers: [{ id: "s1", slug: "loja", display_name: "Loja", role: "revoked" }]
    });
    expect(await call()).toBeNull();
  });

  it("recusa membership com campos vazios", async () => {
    readDomainIdentity.mockResolvedValueOnce(identityPayload());
    listDomainSellers.mockResolvedValueOnce({
      sellers: [{ id: "", slug: "", display_name: "", role: "seller_owner" }]
    });
    expect(await call()).toBeNull();
  });

  it("descarta papel de plataforma desconhecido em vez de repassá-lo", async () => {
    readDomainIdentity.mockResolvedValueOnce(identityPayload(["platform_superadmin", "root"]));
    listDomainSellers.mockResolvedValueOnce({ sellers: [] });
    expect((await call())?.platformRoles).toEqual(["platform_superadmin"]);
  });
});
