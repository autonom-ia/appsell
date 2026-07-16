import { publicDemoRuntimeEnabled } from "./public-demo-runtime";

export type DemoPersona = "admin" | "buyer" | "superadmin";

export const DEMO_ADMIN_SUBJECT = "demo-admin-v1";
export const DEMO_BUYER_SUBJECT = "demo-user-v1";
export const IDENTITY_DEMO_ADMIN_SUBJECT = "demo-admin";
export const IDENTITY_DEMO_BUYER_SUBJECT = "demo-user";
export const DEMO_SUPERADMIN_SUBJECT = "demo-superadmin";
export const DEMO_ADMIN_EMAIL = "admin.demo@autonomia.site";
export const DEMO_BUYER_EMAIL = "user.demo@autonomia.site";
export const DEMO_SUPERADMIN_EMAIL = "comercial@autonomia.site";

export interface DemoProfile {
  persona: DemoPersona;
  subject: string;
  email: string;
  label: string;
  home: "/dashboard" | "/library" | "/platform/demo-inspection";
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
  },
  superadmin: {
    persona: "superadmin",
    subject: DEMO_SUPERADMIN_SUBJECT,
    email: DEMO_SUPERADMIN_EMAIL,
    label: "Superadmin simulado",
    home: "/platform/demo-inspection"
  }
};

export function demoModeEnabled(environment: Partial<Record<string, string | undefined>> = process.env) {
  return (localCiRuntime(environment) || publicDemoRuntimeEnabled(environment)) &&
    environment.APPSELL_DEMO_ENABLED?.toLowerCase() === "true";
}

export function identityDemoModeEnabled(environment: Partial<Record<string, string | undefined>> = process.env) {
  return localCiRuntime(environment) &&
    environment.APPSELL_IDENTITY_DEMO_ENABLED?.toLowerCase() === "true";
}

function localCiRuntime(environment: Partial<Record<string, string | undefined>>) {
  return (environment.NODE_ENV === "development" || environment.NODE_ENV === "test") &&
    (environment.APP_ENV === "local" || environment.APP_ENV === "ci");
}

export function demoProfileForPersona(
  value: string | null | undefined,
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  if (!demoModeEnabled(environment) ||
      (value !== "admin" && value !== "buyer" && value !== "superadmin") ||
      (value === "superadmin" && environment.APPSELL_DEMO_SUPERADMIN_ENABLED?.toLowerCase() !== "true")) {
    return null;
  }
  return profiles[value];
}

export function demoProfileForSubject(
  subject: string | null | undefined,
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  if (!demoModeEnabled(environment) && !identityDemoModeEnabled(environment)) return null;
  if (subject === DEMO_ADMIN_SUBJECT || subject === IDENTITY_DEMO_ADMIN_SUBJECT) return profiles.admin;
  if (subject === DEMO_BUYER_SUBJECT || subject === IDENTITY_DEMO_BUYER_SUBJECT) return profiles.buyer;
  if ((demoModeEnabled(environment) || identityDemoModeEnabled(environment)) &&
      subject === DEMO_SUPERADMIN_SUBJECT &&
      environment.APPSELL_DEMO_SUPERADMIN_ENABLED?.toLowerCase() === "true") {
    return profiles.superadmin;
  }
  return null;
}
