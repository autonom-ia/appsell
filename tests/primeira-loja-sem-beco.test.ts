import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const root = resolve(__dirname, "..");

describe("da primeira loja ao primeiro produto", () => {
  it("emite as permissões de produto no momento em que a loja nasce", async () => {
    // Sem isto, quem cria a primeira loja só recebe as permissões no próximo
    // login: o formulário manda CSRF vazio, leva 403, e recarregar não resolve
    // porque nenhuma outra rota emite cookie. É o beco sem saída um passo
    // adiante do que esta issue corrigiu.
    const rota = readFileSync(resolve(root, "app/api/sellers/route.ts"), "utf8");
    const criacao = rota.slice(rota.indexOf("status: 201"));
    expect(criacao).toContain("grantProductMutationCookies");
    for (const cookie of [
      "productCsrfCookieName",
      "productUpdateCsrfCookieName",
      "productPublishCsrfCookieName"
    ]) {
      expect(rota).toContain(cookie);
    }
  });

  it("não emite essas permissões na listagem, que é leitura", () => {
    const rota = readFileSync(resolve(root, "app/api/sellers/route.ts"), "utf8");
    const leitura = rota.slice(rota.indexOf("export async function GET"), rota.indexOf("export async function POST"));
    expect(leitura).not.toContain("grantProductMutationCookies");
  });
});

describe("papéis que a borda aceita", () => {
  it("recusa papel de loja fora da constraint do banco", async () => {
    const { SELLER_ROLES, PLATFORM_ROLES } = await import("../lib/rbac-roles");
    // "revoked" é status, não papel; aceitá-lo daria audiência de vendedor a
    // uma membership que o banco não reconhece.
    expect(SELLER_ROLES.has("revoked")).toBe(false);
    expect(SELLER_ROLES.has("seller_owner")).toBe(true);
    expect(PLATFORM_ROLES.has("platform_superadmin")).toBe(true);
    expect(PLATFORM_ROLES.has("admin")).toBe(false);
  });

  it("mantém a lista alinhada com a migration que a define", () => {
    const migration = readFileSync(
      resolve(root, "apps/api/migrations/versions/0001_domain_foundation.py"),
      "utf8"
    );
    for (const role of ["seller_owner", "seller_admin", "seller_editor", "seller_support"]) {
      expect(migration).toContain(role);
    }
    for (const role of ["platform_superadmin", "platform_support", "platform_auditor"]) {
      expect(migration).toContain(role);
    }
  });
});

describe("fronteira protegida", () => {
  it("cobre a rota de criação de loja no proxy", () => {
    // O route group (protected) só aplica layout; quem autoriza é o proxy.
    const proxy = readFileSync(resolve(root, "proxy.ts"), "utf8");
    expect(proxy).toContain("/settings/:path*");
  });
});
