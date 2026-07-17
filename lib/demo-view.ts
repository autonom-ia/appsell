import { demoProfileForSubject } from "./demo-config";
import { publicDemoRuntimeEnabled } from "./public-demo-runtime";

export type DemoView = "seller-preview" | "buyer-preview";

export function canAccessDemoPreview(
  subject: string | undefined,
  environment: string | undefined,
  variables: Partial<Record<string, string | undefined>> = process.env
) {
  if (environment === "production") {
    return publicDemoRuntimeEnabled(variables) && Boolean(demoProfileForSubject(subject, variables));
  }
  return subject === "dev-user-001" || Boolean(demoProfileForSubject(subject, variables));
}
