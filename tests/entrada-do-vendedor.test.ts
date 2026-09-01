import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { NEW_STORE_PATH } from "../app/components/app-shell";
import { safePostLoginPath } from "../lib/post-login-redirect";

const root = resolve(__dirname, "..");
const ORIGIN = "https://sell.autonomia.site";

describe("entrada do vendedor", () => {
  it("tem rota própria de criação de loja", () => {
    // O cartão de criar loja vivia dentro de /dashboard e /products, as duas
    // telas que a navegação por papel esconde de quem ainda não tem loja.
    expect(existsSync(resolve(root, `app/(protected)${NEW_STORE_PATH}/page.tsx`))).toBe(true);
  });

  it("leva todo Vender agora da página pública para a criação da loja", () => {
    const page = readFileSync(resolve(root, "app/page.tsx"), "utf8");
    const entradas = page.match(/\/auth\/signup[^"']*/g) ?? [];
    expect(entradas.length).toBeGreaterThan(0);
    for (const entrada of entradas) {
      const destino = new URL(entrada, ORIGIN).searchParams.get("next");
      expect(destino).toBe(NEW_STORE_PATH);
    }
  });

  it("aceita esse destino como caminho de pós-login válido", () => {
    // Se a allowlist recusasse, o cadastro cairia no painel vazio em silêncio.
    expect(safePostLoginPath(NEW_STORE_PATH, ORIGIN)).toBe(NEW_STORE_PATH);
  });

  it("continua recusando destino externo depois da mudança", () => {
    expect(safePostLoginPath("//evil.test/x", ORIGIN)).toBeNull();
    expect(safePostLoginPath("https://evil.test/x", ORIGIN)).toBeNull();
  });

  it("não deixa a criação de loja depender das telas que somem", () => {
    const novaLoja = readFileSync(
      resolve(root, `app/(protected)${NEW_STORE_PATH}/page.tsx`),
      "utf8"
    );
    expect(novaLoja).toContain("SellerOnboardingCard");
  });
});
