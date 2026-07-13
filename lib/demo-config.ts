export type DemoPersona = "admin" | "buyer";

export const DEMO_ADMIN_SUBJECT = "demo-admin-v1";
export const DEMO_BUYER_SUBJECT = "demo-user-v1";
export const DEMO_ADMIN_EMAIL = "admin.demo@autonomia.site";
export const DEMO_BUYER_EMAIL = "user.demo@autonomia.site";

export interface DemoProfile {
  persona: DemoPersona;
  subject: string;
  email: string;
  label: string;
  home: "/dashboard" | "/library";
}

const profiles: Record<DemoPersona, DemoProfile> = {
  admin: {
    persona: "admin",
    subject: DEMO_ADMIN_SUBJECT,
    email: DEMO_ADMIN_EMAIL,
    label: "Administrador",
    home: "/dashboard"
  },
  buyer: {
    persona: "buyer",
    subject: DEMO_BUYER_SUBJECT,
    email: DEMO_BUYER_EMAIL,
    label: "Aluno",
    home: "/library"
  }
};

export function demoModeEnabled(environment: Partial<Record<string, string | undefined>> = process.env) {
  return environment.NODE_ENV !== "production" &&
    environment.APPSELL_DEMO_ENABLED?.toLowerCase() === "true";
}

export function demoProfileForPersona(
  value: string | null | undefined,
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  if (!demoModeEnabled(environment) || (value !== "admin" && value !== "buyer")) return null;
  return profiles[value];
}

export function demoProfileForSubject(
  subject: string | null | undefined,
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  if (!demoModeEnabled(environment)) return null;
  if (subject === DEMO_ADMIN_SUBJECT) return profiles.admin;
  if (subject === DEMO_BUYER_SUBJECT) return profiles.buyer;
  return null;
}
