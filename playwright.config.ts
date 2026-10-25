import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  // Uma quebra que pega a suíte inteira (o login de todos os specs, por
  // exemplo) falhava 238 testes de 30 s cada, com retry, e passava das 2 h do
  // job. Parar nas 10 primeiras falhas não aprova nada: o job continua
  // vermelho, só que em minutos.
  maxFailures: process.env.CI ? 10 : undefined,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure"
  },
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:3000",
    // Desde o #350, sessão sem papel é falha fechada: sem menu de vendedor e
    // sem CSRF de produto. Os specs entram como persona demo, que só existe em
    // NODE_ENV development/test com APP_ENV local/ci (lib/demo-config.ts;
    // tests/demo-config.test.ts prova a recusa em produção). A sessão sem
    // papel continua coberta em tests/e2e/sessao-sem-papel.spec.ts.
    env: { APP_ENV: "local", APPSELL_DEMO_ENABLED: "true" },
    reuseExistingServer: false,
    timeout: 120_000
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit-mobile", use: { ...devices["iPhone 15"] } }
  ]
});
