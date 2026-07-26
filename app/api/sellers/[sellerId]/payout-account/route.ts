import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  bindSellerPayoutAccount,
  DomainApiError,
  readSellerPayoutAccount,
  SellerPayoutAccountProjection
} from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import {
  CONNECT_PAYOUT_ACCOUNT_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { StripeConfigError } from "@/lib/stripe-config.server";
import {
  createConnectedAccount,
  createOnboardingAccountSession,
  deleteConnectedAccountBestEffort
} from "@/lib/stripe-connect.server";

// "Conectar conta Autonom.ia" — the server half of the producer onboarding.
//
// The POST takes NO business input from the browser (the only field is the
// CSRF token): which provider account gets bound is decided exclusively by the
// authenticated accounts.create response, so a tampered request body can never
// choose the destination of a seller's money. Re-entry is safe: if a binding
// already exists, the route just mints a fresh Account Session for the SAME
// bound account, so a producer who dropped mid-KYC resumes where they left off.

interface RouteContext {
  params: Promise<{ sellerId: string }>;
}

const FIELDS = ["_csrf"] as const;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export async function GET(request: NextRequest, context: RouteContext) {
  const { sellerId } = await context.params;
  if (!UUID_PATTERN.test(sellerId)) return notFound();
  try {
    const authenticated = await session(request);
    if (!authenticated) return unauthorized();
    const { payout_account } = await readSellerPayoutAccount(
      authenticated.sessionId,
      authenticated.value,
      sellerId
    );
    return NextResponse.json(
      {
        payout_account,
        csrf: {
          connect: csrfTokenForSession(
            authenticated.sessionId,
            CONNECT_PAYOUT_ACCOUNT_CSRF_ACTION
          )
        }
      },
      { headers: noStore() }
    );
  } catch (error) {
    return payoutError(error);
  }
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { sellerId } = await context.params;
  if (!UUID_PATTERN.test(sellerId)) return notFound();
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return unauthorized();
  try {
    if (!hasConfiguredOrigin(request, configuredPublicOrigin())) return invalidCsrf();
    const value = await oauthService().getSession(sessionId);
    if (!value) return unauthorized();
    let form: FormData | null;
    try {
      form = await readLimitedForm(request);
    } catch (error) {
      return error instanceof PayloadTooLargeError ? tooLarge() : invalid();
    }
    if (!form) return invalid();
    const keys = [...form.keys()];
    if (keys.length !== FIELDS.length || !keys.every((key) => key === "_csrf")) {
      return invalid();
    }
    const csrf = form.get("_csrf");
    if (
      !verifyMutation(
        request,
        sessionId,
        CONNECT_PAYOUT_ACCOUNT_CSRF_ACTION,
        typeof csrf === "string" ? csrf : null
      )
    ) {
      return invalidCsrf();
    }

    const existing = await readSellerPayoutAccount(sessionId, value, sellerId);
    if (existing.payout_account) {
      // Resume path: the account is already bound; a fresh Account Session
      // lets the producer continue (or revisit) the provider's KYC forms.
      const clientSecret = await createOnboardingAccountSession(
        existing.payout_account.provider_account_id
      );
      return sessionResponse(clientSecret, existing.payout_account);
    }

    // First connect: create the provider account, then bind ITS id — taken
    // from the authenticated response, never from the request.
    const account = await createConnectedAccount();
    let bound: { payout_account_id: string };
    try {
      bound = await bindSellerPayoutAccount(sessionId, value, sellerId, {
        provider: "stripe",
        providerAccountId: account.id,
        chargesEnabled: account.charges_enabled === true,
        payoutsEnabled: account.payouts_enabled === true,
        detailsSubmitted: account.details_submitted === true,
        disabledReason: null
      });
    } catch (error) {
      // The freshly created account was never onboarded; deleting it keeps the
      // provider clean. If a concurrent request won the bind race (409), serve
      // the winner's account instead of failing the producer.
      await deleteConnectedAccountBestEffort(account.id);
      if (error instanceof DomainApiError && error.status === 409) {
        const winner = await readSellerPayoutAccount(sessionId, value, sellerId);
        if (winner.payout_account) {
          const clientSecret = await createOnboardingAccountSession(
            winner.payout_account.provider_account_id
          );
          return sessionResponse(clientSecret, winner.payout_account);
        }
      }
      throw error;
    }
    const clientSecret = await createOnboardingAccountSession(account.id);
    return sessionResponse(clientSecret, {
      payout_account_id: bound.payout_account_id,
      provider: "stripe",
      provider_account_id: account.id,
      charges_enabled: account.charges_enabled === true,
      payouts_enabled: account.payouts_enabled === true,
      details_submitted: account.details_submitted === true,
      disabled_reason: null,
      status_synced_at: new Date().toISOString()
    });
  } catch (error) {
    return payoutError(error);
  }
}

async function session(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const value = await oauthService().getSession(sessionId);
  return value ? { sessionId, value } : null;
}

function sessionResponse(
  clientSecret: string,
  payoutAccount: SellerPayoutAccountProjection
) {
  return NextResponse.json(
    { client_secret: clientSecret, payout_account: payoutAccount },
    { headers: noStore() }
  );
}

function noStore() {
  return { "cache-control": "private, no-store" };
}
function notFound() {
  return NextResponse.json({ error: "not_found" }, { status: 404, headers: noStore() });
}
function unauthorized() {
  return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: noStore() });
}
function invalid() {
  return NextResponse.json(
    { error: "invalid_payout_request" },
    { status: 422, headers: noStore() }
  );
}
function invalidCsrf() {
  return NextResponse.json({ error: "invalid_csrf" }, { status: 403, headers: noStore() });
}
function tooLarge() {
  return NextResponse.json(
    { error: "payload_too_large" },
    { status: 413, headers: noStore() }
  );
}
function payoutError(error: unknown) {
  if (error instanceof DomainApiError) {
    return NextResponse.json(
      { error: error.code },
      { status: error.status, headers: noStore() }
    );
  }
  if (error instanceof StripeConfigError) {
    return NextResponse.json(
      { error: "payout_provider_unconfigured" },
      { status: 503, headers: noStore() }
    );
  }
  const code = isOAuthStoreUnavailable(error)
    ? "oauth_store_unavailable"
    : "payout_provider_unavailable";
  return NextResponse.json({ error: code }, { status: 503, headers: noStore() });
}
