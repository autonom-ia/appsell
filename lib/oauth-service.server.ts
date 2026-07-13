import "server-only";

import { IdentityOAuthClient, parseIdentityOAuthConfig } from "./identity-oauth-client.server";
export {
  isOAuthRuntimeConfigurationError,
  oauthRuntimeMode
} from "./oauth-runtime-mode";
import { RedisOAuthService } from "./redis-oauth-service";

declare global {
  var __appsellRedisOAuth: RedisOAuthService | undefined;
  var __appsellIdentityOAuth: IdentityOAuthClient | undefined;
}

export function oauthService() {
  globalThis.__appsellRedisOAuth ??= RedisOAuthService.fromEnvironment();
  return globalThis.__appsellRedisOAuth;
}

export function identityOAuthClient() {
  globalThis.__appsellIdentityOAuth ??= new IdentityOAuthClient(parseIdentityOAuthConfig());
  return globalThis.__appsellIdentityOAuth;
}
