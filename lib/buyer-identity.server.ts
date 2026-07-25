import "server-only";

import type { NextRequest } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { oauthService } from "@/lib/oauth-service.server";

// Resolves the authenticated buyer subject to bind a purchase to.
//
// Why this exists at all: migration 0020's worker computes the entitlement
// owner as `identity_user_id(issuer, coalesce(buyer_identity_sub,
// 'registration:' || buyer_email))` — a deterministic uuid5. So emitting null
// grants the course to the synthetic `registration:<email>` identity, which is a
// DIFFERENT row from the buyer's real login. Paying while signed in and then
// finding nothing in /library is a paid-without-usable-access bug, which is why
// both checkout paths must forward the real subject when there is one.
//
// Only binds when the session's VERIFIED e-mail matches the purchase e-mail.
// Without that check, anyone holding a paid order id could attach it to their
// own session (0020 prioritises an explicit sub over the derived one).
// No match, no session, or infra failure => null, and the worker falls back to
// registration:<e-mail>. Deterministic: same purchase + same session => same sub.
//
// Extracted from app/api/checkout/complete/route.ts so the Stripe checkout uses
// the identical rule instead of a second, divergent copy.

// Anonymous registration-checkout sessions mint subjects with this prefix; they
// must never travel as buyer_identity_sub (the domain rejects them — it is the
// worker's own reserved namespace).
const REGISTRATION_SUBJECT_PREFIX = "registration:";

export async function resolveBuyerIdentitySub(
  request: NextRequest,
  purchaseBuyerEmail: string
): Promise<string | null> {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  try {
    const session = await oauthService().getSession(sessionId);
    const subject = session?.subject;
    const identity = session?.identity;
    if (typeof subject !== "string" || subject.length === 0) return null;
    if (subject.startsWith(REGISTRATION_SUBJECT_PREFIX)) return null;
    if (
      !identity
      || identity.emailVerified !== true
      || typeof identity.email !== "string"
      || identity.email.trim().toLowerCase() !== purchaseBuyerEmail.trim().toLowerCase()
    ) {
      return null;
    }
    return subject;
  } catch {
    // OAuth/session infra errors (e.g. Redis unavailable) must not block an
    // otherwise-anonymous checkout — fall back to no identity.
    return null;
  }
}
