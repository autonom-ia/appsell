import { defineConfig, devices } from "@playwright/test";

const sellOrigin = process.env.E2E_REAL_SELL_ORIGIN ?? "http://localhost:3200";

export default defineConfig({
  testDir: "./tests/e2e-real",
  outputDir: "output/playwright/identity-real-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  reporter: [["list"], ["html", { outputFolder: "output/playwright/identity-real-report", open: "never" }]],
  use: {
    baseURL: sellOrigin,
    ...devices["iPhone 15"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off"
  }
});
