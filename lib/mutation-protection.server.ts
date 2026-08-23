import "server-only";

import { deriveCsrfToken, parseCsrfKey, verifyCsrfToken } from "./csrf";
import { configuredPublicOrigin, hasConfiguredOrigin } from "./public-origin";

export const LOGOUT_CSRF_ACTION = "POST:/auth/logout";
export const ONBOARD_SELLER_CSRF_ACTION = "POST:/api/sellers";
export const CREATE_PRODUCT_DRAFT_CSRF_ACTION = "POST:/api/products";
export const UPDATE_PRODUCT_DRAFT_CSRF_ACTION = "PATCH:/api/products";
export const PUBLISH_PRODUCT_DRAFT_CSRF_ACTION = "POST:/api/products/publications";
export const TRANSITION_OFFER_STATUS_CSRF_ACTION = "PATCH:/api/products/offers/status";
export const CREATE_CURRICULUM_CSRF_ACTION = "POST:/api/curriculum";
export const UPDATE_CURRICULUM_CSRF_ACTION = "PATCH:/api/curriculum";
export const REORDER_CURRICULUM_CSRF_ACTION = "PUT:/api/curriculum/order";
export const ARCHIVE_CURRICULUM_CSRF_ACTION = "POST:/api/curriculum/archive";
export const CREATE_ASSET_UPLOAD_CSRF_ACTION = "POST:/api/assets/upload-intents";
export const CONFIRM_ASSET_UPLOAD_CSRF_ACTION = "POST:/api/assets/uploads/confirm";
export const CANCEL_ASSET_UPLOAD_CSRF_ACTION = "POST:/api/assets/uploads/cancel";
export const ARCHIVE_ASSET_CSRF_ACTION = "POST:/api/assets/archive";
export const UPDATE_ASSET_POLICY_CSRF_ACTION = "PATCH:/api/assets/policy";
export const UPDATE_LEARNING_PROGRESS_CSRF_ACTION = "PUT:/api/library/progress";
export const COMPLETE_LEARNING_LESSON_CSRF_ACTION = "POST:/api/library/complete";
export const START_DEMO_INSPECTION_CSRF_ACTION = "POST:/api/platform/demo-inspections";
export const REVOKE_DEMO_INSPECTION_CSRF_ACTION = "POST:/api/platform/demo-inspections/revoke";
export const COMPLETE_DEMO_CHECKOUT_CSRF_ACTION = "POST:/api/demo-checkout";
export const ENROLL_PUBLIC_PRODUCT_CSRF_ACTION = "POST:/api/library/enroll";
export const CONNECT_PAYOUT_ACCOUNT_CSRF_ACTION = "POST:/api/sellers/payout-account";

export function csrfTokenForSession(sessionId: string, action: string) {
  return deriveCsrfToken(sessionId, action, csrfKey());
}

export function verifyMutation(
  request: Request,
  sessionId: string | undefined,
  action: string,
  token: string | null | undefined
) {
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) return false;
  return verifyCsrfToken(token, sessionId, action, csrfKey());
}

function csrfKey() {
  return parseCsrfKey(
    process.env.APPSELL_CSRF_HMAC_KEY_B64 ?? "",
    configuredNonCsrfKeys()
  );
}

function configuredNonCsrfKeys() {
  const keys: Buffer[] = [];
  const sessionKey = decodeKey(process.env.APPSELL_SESSION_HMAC_KEY_B64);
  if (sessionKey) keys.push(sessionKey);

  for (const serialized of [
    process.env.APPSELL_VAULT_LOCAL_KEYRING_B64_JSON,
    process.env.APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON
  ]) {
    try {
      const keyring = JSON.parse(serialized ?? "[]");
      if (Array.isArray(keyring)) {
        for (const entry of keyring) {
          const key = decodeKey(
            typeof entry === "object" && entry !== null && "key" in entry
              ? String(entry.key)
              : undefined
          );
          if (key) keys.push(key);
        }
      }
    } catch {
      // Each owning subsystem has its own fail-closed parser.
    }
  }

  return keys;
}

function decodeKey(encoded: string | undefined) {
  if (!encoded) return undefined;
  const decoded = Buffer.from(encoded, "base64");
  return decoded.length === 32 ? decoded : undefined;
}
