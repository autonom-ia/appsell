import { OAuthFlowError } from "./oauth-service";

// Decides whether a failed callback should silently restart the login instead
// of answering with an error body.
//
// The case this exists for: a sign-up. Creating the account, waiting for the
// confirmation e-mail and confirming it takes longer than the login
// transaction TTL, so the browser returns from the provider with a valid code
// while this app no longer has the transaction. The user did everything right,
// so showing raw JSON is wrong — restarting the login completes silently while
// the provider session is valid.
//
// Restarting is only safe once per attempt: `alreadyRestarted` carries the
// one-shot guard cookie, so a failure that survives the restart falls through
// to the error body instead of bouncing the browser forever.
export function shouldRestartLogin(error: unknown, alreadyRestarted: boolean): boolean {
  if (alreadyRestarted) return false;
  return error instanceof OAuthFlowError && error.failure === "recoverable";
}
