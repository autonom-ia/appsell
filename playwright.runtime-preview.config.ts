import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const origin = process.env.E2E_RUNTIME_ORIGIN ?? "http://localhost:13200";
const evidence = path.resolve(
  process.env.E2E_RUNTIME_EVIDENCE_DIR ?? "output/playwright/runtime-preview-evidence"
);

export default defineConfig({
  testDir: "./tests/e2e-runtime-preview",
  outputDir: path.join(evidence, "results"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  reporter: [["list"], ["html", {
    outputFolder: path.join(evidence, "report"),
    open: "never"
  }]],
  use: {
    baseURL: origin,
    ...devices["iPhone 15"],
    browserName: "chromium",
    launchOptions: {
      args: ["--host-resolver-rules=MAP sell.autonomia.site 127.0.0.1"]
    },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off"
  }
});
