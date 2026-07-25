// Records why an OAuth callback failed. The HTTP response stays deliberately
// coarse (a misconfiguration oracle must not be readable by an anonymous
// caller), so without this the reason was discarded and every failure looked
// like `invalid_oauth_callback` in production.
//
// Only allowlisted, value-free strings reach the sink: the public outcome code
// and the adapter's fixed error message (which names the failed claim, never a
// token, claim value or configured expectation).

import {
  IdentityOAuthAmbiguousError,
  IdentityOAuthConfigurationError,
  IdentityOAuthProtocolError,
  IdentityOAuthUnavailableError
} from "./identity-oauth-client.server";
import { OAuthRuntimeConfigurationError } from "./oauth-runtime-mode";
import { OAuthFlowError } from "./oauth-service";

export interface OAuthCallbackFailure {
  /** Public error code returned to the caller, e.g. "invalid_oauth_callback". */
  readonly outcome: string;
  /** Internal, value-free reason, e.g. "Identity token verification failed (claim iss)." */
  readonly reason: string;
}

export type TelemetrySink = (line: string) => void;

// Matched by identity, not by name: `name` is a writable property, so an error
// crafted elsewhere could otherwise claim to be an adapter error and get its
// message recorded verbatim.
const ADAPTER_ERROR_TYPES = [
  IdentityOAuthProtocolError,
  IdentityOAuthConfigurationError,
  IdentityOAuthUnavailableError,
  IdentityOAuthAmbiguousError,
  OAuthRuntimeConfigurationError,
  OAuthFlowError
] as const;

/**
 * Derives a value-free reason from a thrown error. Adapter errors carry fixed,
 * allowlisted messages and are safe to record verbatim; anything else is
 * reduced to its constructor name so an unexpected message can never leak.
 */
export function describeCallbackFailure(error: unknown): string {
  if (!(error instanceof Error)) return "non-error thrown";
  const isAdapterError = ADAPTER_ERROR_TYPES.some((type) => error instanceof type);
  // Constant fallback: `constructor` and `name` are both writable, so anything
  // read off an unknown error is attacker-shaped. Unknown means unknown.
  return isAdapterError ? error.message : "unclassified error";
}

export function reportOAuthCallbackFailure(
  failure: OAuthCallbackFailure,
  sink: TelemetrySink = (line) => console.error(line)
): void {
  sink(JSON.stringify({ event: "oauth_callback_failed", ...failure }));
}
