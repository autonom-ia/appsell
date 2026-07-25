// Records why an OAuth callback failed. The HTTP response stays deliberately
// coarse (a misconfiguration oracle must not be readable by an anonymous
// caller), so without this the reason was discarded and every failure looked
// like `invalid_oauth_callback` in production.
//
// Only allowlisted, value-free strings reach the sink: the public outcome code
// and the adapter's fixed error message (which names the failed claim, never a
// token, claim value or configured expectation).

export interface OAuthCallbackFailure {
  /** Public error code returned to the caller, e.g. "invalid_oauth_callback". */
  readonly outcome: string;
  /** Internal, value-free reason, e.g. "Identity token verification failed (claim iss)." */
  readonly reason: string;
}

export type TelemetrySink = (line: string) => void;

const ADAPTER_ERROR_NAMES = new Set([
  "IdentityOAuthProtocolError",
  "IdentityOAuthConfigurationError",
  "IdentityOAuthUnavailableError",
  "IdentityOAuthAmbiguousError",
  "OAuthRuntimeConfigurationError",
  "OAuthFlowError"
]);

/**
 * Derives a value-free reason from a thrown error. Adapter errors carry fixed,
 * allowlisted messages and are safe to record verbatim; anything else is
 * reduced to its constructor name so an unexpected message can never leak.
 */
export function describeCallbackFailure(error: unknown): string {
  if (!(error instanceof Error)) return "non-error thrown";
  const name = error.constructor?.name ?? error.name;
  return ADAPTER_ERROR_NAMES.has(name) || ADAPTER_ERROR_NAMES.has(error.name)
    ? error.message
    : name;
}

export function reportOAuthCallbackFailure(
  failure: OAuthCallbackFailure,
  sink: TelemetrySink = (line) => console.error(line)
): void {
  sink(JSON.stringify({ event: "oauth_callback_failed", ...failure }));
}
