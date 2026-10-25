import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/production",
  // Its own folder: Playwright empties outputDir at start, and this run comes
  // after test:e2e in CI, whose failed-attempt traces must survive (#519).
  outputDir: "test-results/production",
  fullyParallel: false,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure"
  },
  webServer: {
    command: "pnpm start",
    url: "http://localhost:3000",
    reuseExistingServer: false,
    timeout: 120_000
  },
  projects: [{ name: "production-chromium", use: { ...devices["Desktop Chrome"] } }]
});
