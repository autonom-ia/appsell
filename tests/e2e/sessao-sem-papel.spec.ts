import { expect, test } from "@playwright/test";
import { login } from "./support/login";

// O e2e entra como persona demo (support/login.ts). As duas regras de
// segurança do #350 que isso tiraria de cena ficam provadas aqui.

// A resposta real do /api/session para uma sessão local sem persona e sem
// Identity, lida num trace do e2e em 24/10/2026. É a sessão que o shell não
// sabe ler e por isso tem de tratar como não validada.
const SESSAO_SEM_PAPEL = {
  authenticated: true,
  expiresAt: 1_792_848_188_064,
  runtime: "local",
  demoProfile: null,
  identity: null
};

test("uma sessão sem papel não ganha o menu do vendedor", async ({ page }) => {
  await login(page);
  await page.route("**/api/session", async (route) => {
    await route.fulfill({ json: SESSAO_SEM_PAPEL });
  });

  await page.goto("/dashboard");

  // Falha fechada (app-shell.tsx): adivinhar o papel aqui seria mostrar o
  // menu de vendedor a um aluno.
  await expect(page.locator(".nav-eyebrow")).toHaveText("Acesso indisponível");
  await expect(page.getByRole("alert").getByText("Não foi possível validar seu acesso.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Produtos" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Navegação móvel" }).getByRole("link", { name: "Produtos" }))
    .toHaveCount(0);
});

test("o comprador sai do login sem nenhum CSRF de produto nem de loja", async ({ context, page }) => {
  await login(page, "buyer");

  const nomes = (await context.cookies()).map((cookie) => cookie.name);
  // A sessão existe: sem isto, "nenhum cookie de produto" passaria por um
  // login que simplesmente falhou.
  expect(nomes).toContain("appsell_session");
  // Com CSRF de produto o comprador sairia do login com poder de criar,
  // editar e publicar catálogo (auth/callback/route.ts, sellerCookiePlan).
  expect(nomes.filter((nome) => nome.startsWith("appsell_product_"))).toEqual([]);
  expect(nomes).not.toContain("appsell_seller_csrf");
});
