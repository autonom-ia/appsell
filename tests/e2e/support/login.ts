import { expect, type Page } from "@playwright/test";

type Persona = "admin" | "buyer";

const HOME: Record<Persona, string> = { admin: "/dashboard", buyer: "/library" };

/**
 * Entra no ambiente local como persona demo, pela rota de login, não por um
 * link da página pública. A página pública é texto de venda e muda: quando o
 * #344 trocou "Entrar no ambiente local" por "Entrar", todo o e2e passou a
 * esperar um elemento que não existia mais.
 *
 * A persona é obrigatória porque, desde o #350, uma sessão sem papel não
 * recebe menu de vendedor nem CSRF de produto (falha fechada). Essa sessão é
 * conferida à parte, em sessao-sem-papel.spec.ts.
 */
export async function login(page: Page, persona: Persona = "admin"): Promise<void> {
  await page.goto(`/auth/login?persona=${persona}`);
  await expect(page).toHaveURL(HOME[persona]);
}
