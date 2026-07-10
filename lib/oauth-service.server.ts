import "server-only";

import { DevOAuthService, OAuthFlowError } from "./oauth-service";

declare global {
  var __appsellDevOAuth: DevOAuthService | undefined;
}

export function oauthService() {
  if (process.env.NODE_ENV === "production") {
    throw new OAuthFlowError("Development Identity mock is disabled in production.");
  }
  globalThis.__appsellDevOAuth ??= new DevOAuthService();
  return globalThis.__appsellDevOAuth;
}
