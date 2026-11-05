import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/production",
  fullyParallel: false,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure"
  },
  webServer: {
    command: "pnpm start",
    // #544: the web refuses to pick a checkout provider on its own.
    env: { APPSELL_CHECKOUT_PROVIDER: process.env.APPSELL_CHECKOUT_PROVIDER ?? "local_stripe" },
    url: "http://localhost:3000",
    reuseExistingServer: false,
    timeout: 120_000
  },
  projects: [{ name: "production-chromium", use: { ...devices["Desktop Chrome"] } }]
});
