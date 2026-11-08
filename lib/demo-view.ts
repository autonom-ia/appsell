import { demoProfileForSubject } from "./demo-config";
import { e2eBuildRuntimeEnabled } from "./e2e-build-runtime";
import { publicDemoRuntimeEnabled } from "./public-demo-runtime";

export type DemoView = "seller-preview" | "buyer-preview";

export function canAccessDemoPreview(
  subject: string | undefined,
  environment: string | undefined,
  variables: Partial<Record<string, string | undefined>> = process.env
) {
  if (environment === "production") {
    return (publicDemoRuntimeEnabled(variables) || e2eBuildRuntimeEnabled(variables)) && Boolean(demoProfileForSubject(subject, variables));
  }
  return subject === "dev-user-001" || Boolean(demoProfileForSubject(subject, variables));
}
