import { NextRequest, NextResponse } from "next/server";

import {
  createDomainAccessLink,
  listDomainAccessLinks
} from "@/lib/internal-api-client.server";
import {
  accessLinkUrl,
  mintAccessLinkToken
} from "@/lib/access-link-token.server";
import {
  CREATE_ACCESS_LINK_CSRF_ACTION,
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
  productRouteIds,
  type ProductRouteContext,
  readExactLimitedFields,
  unauthorized
} from "../_route-shared";

const CREATE_FIELDS = ["_csrf", "idempotency_key", "label", "max_uses", "expires_at"] as const;
const MAX_LABEL_BYTES = 80;
const MAX_SEATS = 100_000;

export async function GET(request: NextRequest, context: ProductRouteContext) {
  const ids = await productRouteIds(context);
  if (!ids) return invalidInput("form");
  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();
  try {
    const payload = await listDomainAccessLinks(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}

export async function POST(request: NextRequest, context: ProductRouteContext) {
  const ids = await productRouteIds(context);
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

  const parsed = await readExactLimitedFields(request, CREATE_FIELDS);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  const fields = parsed.fields;
  if (!verifyMutation(request, sessionId, CREATE_ACCESS_LINK_CSRF_ACTION, fields._csrf)) {
    return forbiddenMutation();
  }

  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  if (!idempotencyKey) return invalidInput("form");
  const label = fields.label ?? "";
  if (label !== label.trim() || label.length < 1 ||
      Buffer.byteLength(label, "utf8") > MAX_LABEL_BYTES) {
    return invalidInput("label");
  }
  const maxUses = parseSeats(fields.max_uses);
  if (maxUses === "invalid") return invalidInput("max_uses");
  const expiresAt = parseExpiry(fields.expires_at);
  if (expiresAt === "invalid") return invalidInput("expires_at");

  // Minted here so the raw value exists only in this response. Nothing writes it
  // to a log, a cookie or the database.
  const minted = mintAccessLinkToken();
  try {
    const payload = await createDomainAccessLink(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      {
        idempotencyKey,
        label,
        tokenSha256: minted.tokenSha256,
        tokenPrefix: minted.tokenPrefix,
        maxUses,
        expiresAt
      }
    );
    return NextResponse.json(
      { access_link: payload, url: accessLinkUrl(configuredPublicOrigin() ?? "", minted.token) },
      { status: 201, headers: { "cache-control": "private, no-store, max-age=0" } }
    );
  } catch (error) {
    return mappedError(error);
  }
}

function parseSeats(value: string | undefined): number | null | "invalid" {
  if (value === undefined || value === "") return null;
  if (!/^[1-9][0-9]{0,6}$/.test(value)) return "invalid";
  const parsed = Number(value);
  return parsed <= MAX_SEATS ? parsed : "invalid";
}

function parseExpiry(value: string | undefined): string | null | "invalid" {
  if (value === undefined || value === "") return null;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || parsed <= Date.now()) return "invalid";
  return new Date(parsed).toISOString();
}
