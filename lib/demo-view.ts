import { demoProfileForSubject } from "./demo-config";

export type DemoView = "seller-preview" | "buyer-preview";

export function canAccessDemoPreview(
  subject: string | undefined,
  environment: string | undefined,
  variables: Partial<Record<string, string | undefined>> = process.env
) {
  return environment !== "production" &&
    (subject === "dev-user-001" || Boolean(demoProfileForSubject(subject, variables)));
}
