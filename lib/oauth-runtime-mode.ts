import { capacityLocalCiRuntimeEnabled } from "./capacity-local-ci-runtime";

export type OAuthRuntimeMode = "local" | "identity";

export class OAuthRuntimeConfigurationError extends Error {}

export function oauthRuntimeMode(
  environment: Partial<Record<string, string | undefined>> = process.env
): OAuthRuntimeMode {
  const configured = environment.APPSELL_OAUTH_MODE;
  if (configured === undefined && environment.NODE_ENV !== "production") return "local";
  if (configured !== "local" && configured !== "identity") {
    throw new OAuthRuntimeConfigurationError("APPSELL_OAUTH_MODE must be local or identity.");
  }
  if (configured === "local" && environment.NODE_ENV === "production" &&
      !capacityLocalCiRuntimeEnabled(environment)) {
    throw new OAuthRuntimeConfigurationError("Local OAuth is forbidden in production.");
  }
  return configured;
}

export function isOAuthRuntimeConfigurationError(error: unknown) {
  return error instanceof OAuthRuntimeConfigurationError;
}
