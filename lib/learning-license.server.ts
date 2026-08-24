import "server-only";

import { createHash } from "node:crypto";

import { demoProfileForSubject } from "./demo-config";

interface LearningLicenseViewer {
  subject: string;
  identity?: {
    email: string;
    emailVerified: true;
  } | null;
}

export function learningLicenseLabel(
  viewer: LearningLicenseViewer,
  entitlementId: string
) {
  const trace = createHash("sha256")
    .update(`${viewer.subject}:${entitlementId}`, "utf8")
    .digest("hex")
    .slice(0, 6);
  const email = viewer.identity?.emailVerified === true
    ? viewer.identity.email
    : demoProfileForSubject(viewer.subject)?.email;
  const licensee = email ? maskLicenseEmail(email) : "acesso protegido";
  return `Licenciado para ${licensee} • ID ${trace}`;
}

export function maskLicenseEmail(value: string) {
  const separator = value.lastIndexOf("@");
  if (separator < 1 || separator === value.length - 1) return "acesso protegido";
  const local = value.slice(0, separator);
  const domain = value.slice(separator + 1);
  const visible = local.slice(0, Math.min(2, local.length));
  return `${visible}***@${domain}`;
}
