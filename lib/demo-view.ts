export type DemoView = "seller-preview" | "buyer-preview";

export function canAccessDemoPreview(subject: string | undefined, environment: string | undefined) {
  return environment !== "production" && subject === "dev-user-001";
}
