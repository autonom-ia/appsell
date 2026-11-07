import { join } from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { E2E_BUILD_PUBLIC_ORIGIN, E2E_BUILD_RUNTIME_MARKER } from "./lib/e2e-build-runtime";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  // Uma quebra que pega a suíte inteira (o login de todos os specs, por
  // exemplo) falhava 238 testes de 30 s cada, com retry, e passava das 2 h do
  // job. Parar nas 10 primeiras falhas não aprova nada: o job continua
  // vermelho, só que em minutos.
  maxFailures: process.env.CI ? 10 : undefined,
  // No CI, o "list" soma ao "github" o projeto, o resultado e a duração de
  // cada teste, inclusive a tentativa que falhou antes do retry: é o que
  // separa teste lento de ambiente lento (#585).
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure"
  },
  webServer: {
    // A suíte roda sobre o build de produção, não sobre o `next dev`: o
    // servidor de desenvolvimento travava ~30 s atendendo o WebKit, e era a
    // origem dos testes instáveis do webkit-mobile (#585). No CI o build já
    // foi feito no passo anterior; localmente, este comando o refaz.
    command: process.env.CI ? "pnpm start" : "pnpm build && pnpm start",
    url: "http://localhost:3000",
    // Desde o #350, sessão sem papel é falha fechada: sem menu de vendedor e
    // sem CSRF de produto. Os specs entram como persona demo, que o build de
    // produção só aceita com as três travas de lib/e2e-build-runtime.ts:
    // NODE_ENV=production com APP_ENV=ci, origem http://localhost:3000 com
    // OAuth local, e o marcador abaixo. tests/e2e-build-runtime.test.ts prova
    // cada recusa, e tests/production/ prova o mesmo build fechado sem o
    // marcador. A sessão sem papel continua coberta em
    // tests/e2e/sessao-sem-papel.spec.ts.
    // #544: o web não escolhe o provedor do checkout sozinho.
    env: {
      APP_ENV: "ci",
      APPSELL_PUBLIC_ORIGIN: E2E_BUILD_PUBLIC_ORIGIN,
      APPSELL_OAUTH_MODE: "local",
      APPSELL_E2E_BUILD_RUNTIME: E2E_BUILD_RUNTIME_MARKER,
      APPSELL_DEMO_ENABLED: "true",
      // Em produção a raiz dos PDFs da aula é obrigatória; aqui é a mesma
      // pasta que o `next dev` usava por padrão.
      APPSELL_PRIVATE_LEARNING_ASSET_ROOT: join(process.cwd(), "output", "pdf", "codex"),
      APPSELL_CHECKOUT_PROVIDER: process.env.APPSELL_CHECKOUT_PROVIDER ?? "local_stripe"
    },
    reuseExistingServer: false,
    stdout: "ignore",
    timeout: process.env.CI ? 120_000 : 600_000
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit-mobile", use: { ...devices["iPhone 15"] } }
  ]
});
