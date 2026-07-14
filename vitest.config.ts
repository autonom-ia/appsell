import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    clearMocks: true,
    // Several integration files start real Next.js processes and temporarily
    // snapshot Next-generated config. Serial files keep those shared worktree
    // surfaces deterministic while each test still exercises concurrency.
    fileParallelism: false
  }
});
