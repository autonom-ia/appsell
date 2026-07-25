import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // Mirror the tsconfig "@/*" -> "./*" path alias so tests can import route
  // handlers and modules that reference "@/lib/...".
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) }
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    exclude: [
      "tests/bff-api-onboarding.test.ts",
      "tests/bff-buyer-multi-seller.test.ts",
      "tests/bff-capacity-100-tenants.test.ts",
      "tests/bff-demo-library.test.ts",
      "tests/bff-identity-vertical.test.ts",
      "tests/bff-learning-multiprocess.test.ts",
      "tests/bff-multiprocess.test.ts",
      "tests/bff-superadmin-inspection.test.ts",
      "tests/bff-webhook-ingress.test.ts"
    ],
    clearMocks: true,
    // The excluded integration files start real Next processes and mutate
    // Next-generated config. Bound parallelism to avoid CPU-starving tests with
    // intentionally tight 5-second deadlines on standard 4-core runners.
    fileParallelism: true,
    maxWorkers: 4
  }
});
