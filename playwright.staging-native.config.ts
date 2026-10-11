import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const origin = process.env.E2E_RUNTIME_ORIGIN ?? "";
const evidence = path.resolve(
  process.env.E2E_RUNTIME_EVIDENCE_DIR ?? "output/playwright/staging-native-evidence"
);
const basicUser = process.env.E2E_BASIC_AUTH_USER ?? "";
const basicPassword = process.env.E2E_BASIC_AUTH_PASSWORD ?? "";

if (!origin) {
  throw new Error("E2E_RUNTIME_ORIGIN is required for the native staging suite");
}

export default defineConfig({
  testDir: "./tests/e2e-runtime-preview",
  outputDir: path.join(evidence, "results"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  reporter: [["list"], ["html", { outputFolder: path.join(evidence, "report"), open: "never" }]],
  use: {
    baseURL: origin,
    ...devices["iPhone 15"],
    browserName: "chromium",
    httpCredentials: basicUser
      ? { username: basicUser, password: basicPassword, origin }
      : undefined,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off"
  }
});
