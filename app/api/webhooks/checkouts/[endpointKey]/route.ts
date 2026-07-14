import { NextRequest, NextResponse } from "next/server";

import {
  parseCheckoutWebhookHeaders,
  proxyCheckoutWebhook,
  validCheckoutWebhookEndpointKey
} from "@/lib/checkout-webhook-proxy.server";
import { DomainApiError } from "@/lib/internal-api-client.server";
import {
  RawBodyTooLargeError,
  readLimitedRawBody
} from "@/lib/limited-raw-body.server";

interface RouteContext {
  params: Promise<{ endpointKey: string }>;
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { endpointKey } = await context.params;
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  const headers = parseCheckoutWebhookHeaders(request.headers);
  if (!validCheckoutWebhookEndpointKey(endpointKey) || contentType !== "application/json" || !headers) {
    return rejected(400);
  }
  let rawBody: Buffer;
  try {
    rawBody = await readLimitedRawBody(request);
  } catch (error) {
    if (error instanceof RawBodyTooLargeError) return rejected(413);
    return rejected(400);
  }
  try {
    const response = await proxyCheckoutWebhook(endpointKey, rawBody, headers);
    if (response.status === 202 && isRecord(response.payload)) {
      const status = response.payload.status;
      if (status === "accepted" || status === "replayed") {
        return NextResponse.json({ status }, { status: 202, headers: noStore() });
      }
      return rejected(503);
    }
    if (response.status === 409) return rejected(409);
    if (response.status === 413) return rejected(413);
    if (response.status >= 500) return rejected(503);
    return rejected(401);
  } catch (error) {
    if (error instanceof DomainApiError && error.status < 500) return rejected(400);
    return rejected(503);
  }
}

function rejected(status: 400 | 401 | 409 | 413 | 503) {
  const code = status === 409 ? "webhook_event_conflict"
    : status === 413 ? "payload_too_large"
    : status === 503 ? "webhook_unavailable"
    : "webhook_rejected";
  return NextResponse.json({ error: code }, { status, headers: noStore() });
}

function noStore() {
  return { "cache-control": "no-store" };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
