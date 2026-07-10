import "server-only";

import { RedisOAuthService } from "./redis-oauth-service";

declare global {
  var __appsellRedisOAuth: RedisOAuthService | undefined;
}

export function oauthService() {
  globalThis.__appsellRedisOAuth ??= RedisOAuthService.fromEnvironment();
  return globalThis.__appsellRedisOAuth;
}
