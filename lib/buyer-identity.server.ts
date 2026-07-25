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

// Collapsing every non-authenticated case to `null` is safe AFTER the charge
// (the registration fallback is then the only option left) but not BEFORE it: a
// mismatched or unreachable session would silently mint a purchase bound to
// registration:<e-mail>, which no real login can reach. Pre-charge callers need
// to tell the cases apart, so the resolver reports them.
export type BuyerIdentity =
  | { status: "authenticated"; subject: string }
  | { status: "anonymous" }
  | { status: "mismatch" }
  | { status: "unavailable" };

export async function resolveBuyerIdentity(
  request: NextRequest,
  purchaseBuyerEmail: string
): Promise<BuyerIdentity> {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return { status: "anonymous" };
  let session: Awaited<ReturnType<ReturnType<typeof oauthService>["getSession"]>>;
  try {
    session = await oauthService().getSession(sessionId);
  } catch {
    // Session store unreachable (e.g. Redis down). NOT the same as anonymous:
    // the buyer may well be signed in and we simply cannot tell.
    return { status: "unavailable" };
  }
  const subject = session?.subject;
  const identity = session?.identity;
  // Cookie present but no live session (expired/evicted), or an anonymous
  // registration-checkout subject, which belongs to the worker's namespace.
  if (typeof subject !== "string" || subject.length === 0) return { status: "anonymous" };
  if (subject.startsWith(REGISTRATION_SUBJECT_PREFIX)) return { status: "anonymous" };
  if (
    !identity
    || identity.emailVerified !== true
    || typeof identity.email !== "string"
    || identity.email.trim().toLowerCase() !== purchaseBuyerEmail.trim().toLowerCase()
  ) {
    // Signed in, but not as the person being charged. Binding anyway would let
    // anyone holding a paid order id attach it to their own session (0020
    // prioritises an explicit sub over the derived one).
    return { status: "mismatch" };
  }
  return { status: "authenticated", subject };
}

// Post-charge convenience: by then the money is gone and the only question is
// which identity to bind, so anything short of a verified match falls back to
// the worker-derived registration:<e-mail>.
export async function resolveBuyerIdentitySub(
  request: NextRequest,
  purchaseBuyerEmail: string
): Promise<string | null> {
  const identity = await resolveBuyerIdentity(request, purchaseBuyerEmail);
  return identity.status === "authenticated" ? identity.subject : null;
}
