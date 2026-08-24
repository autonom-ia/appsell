import { NextRequest, NextResponse } from "next/server";

import { rotateDomainAccessLink } from "@/lib/internal-api-client.server";
import { accessLinkUrl, mintAccessLinkToken } from "@/lib/access-link-token.server";
import {
  ROTATE_ACCESS_LINK_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { sessionCookieName } from "@/lib/cookies";

import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  mappedError,
  payloadTooLarge,
  readExactLimitedFields,
  unauthorized
} from "../../../_route-shared";
import { accessLinkRouteIds, type AccessLinkRouteContext } from "../_shared";

const FIELDS = ["_csrf", "idempotency_key"] as const;

export async function POST(request: NextRequest, context: AccessLinkRouteContext) {
  const ids = await accessLinkRouteIds(context);
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!ids || !sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return forbiddenMutation();
  }

  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();

  const parsed = await readExactLimitedFields(request, FIELDS);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  if (!verifyMutation(request, sessionId, ROTATE_ACCESS_LINK_CSRF_ACTION, parsed.fields._csrf)) {
    return forbiddenMutation();
  }
  const idempotencyKey = canonicalUuid(parsed.fields.idempotency_key);
  if (!idempotencyKey) return invalidInput("form");

  // A fresh secret replaces the old one. The seats already handed out survive,
  // which is the whole point of rotating instead of creating a new link.
  const minted = mintAccessLinkToken();
  try {
    const payload = await rotateDomainAccessLink(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.accessLinkId,
      {
        idempotencyKey,
        tokenSha256: minted.tokenSha256,
        tokenPrefix: minted.tokenPrefix
      }
    );
    return NextResponse.json(
      { access_link: payload, url: accessLinkUrl(configuredPublicOrigin() ?? "", minted.token) },
      { headers: { "cache-control": "private, no-store, max-age=0" } }
    );
  } catch (error) {
    return mappedError(error);
  }
}
