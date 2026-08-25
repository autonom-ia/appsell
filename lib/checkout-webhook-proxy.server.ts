import "server-only";

import { signInternalEnvelope } from "./internal-envelope";
import { DomainApiError, internalServiceConfig } from "./internal-api-client.server";

const ENDPOINT_KEY = /^[A-Za-z0-9_-]{32,64}$/;
const EVENT_ID = /^[A-Za-z0-9._:-]{1,128}$/;
const TIMESTAMP = /^(0|[1-9][0-9]{0,10})$/;
const SIGNATURE = /^v1=[0-9a-f]{64}$/;

export interface CheckoutWebhookHeaders {
  protocol: "legacy" | "external_checkout";
  eventId: string;
  timestamp: string;
  signature: string;
}

export function parseCheckoutWebhookHeaders(headers: Headers): CheckoutWebhookHeaders | null {
  const external = {
    eventId: headers.get("x-external-checkout-webhook-event-id"),
    timestamp: headers.get("x-external-checkout-webhook-timestamp"),
    signature: headers.get("x-external-checkout-webhook-signature")
  };
  if (external.eventId !== null || external.timestamp !== null || external.signature !== null) {
    if (!EVENT_ID.test(external.eventId ?? "") || !TIMESTAMP.test(external.timestamp ?? "") || !SIGNATURE.test(external.signature ?? "")) {
      return null;
    }
    return {
      protocol: "external_checkout",
      eventId: external.eventId ?? "",
      timestamp: external.timestamp ?? "",
      signature: external.signature ?? ""
    };
  }

  const eventId = headers.get("x-appsell-webhook-event-id") ?? "";
  const timestamp = headers.get("x-appsell-webhook-timestamp") ?? "";
  const signature = headers.get("x-appsell-webhook-signature") ?? "";
  if (!EVENT_ID.test(eventId) || !TIMESTAMP.test(timestamp) || !SIGNATURE.test(signature)) {
    return null;
  }
  return { protocol: "legacy", eventId, timestamp, signature };
}

export function validCheckoutWebhookEndpointKey(value: string) {
  return ENDPOINT_KEY.test(value);
}

export async function proxyCheckoutWebhook(
  endpointKey: string,
  rawBody: Buffer,
  webhookHeaders: CheckoutWebhookHeaders
) {
  if (!validCheckoutWebhookEndpointKey(endpointKey) || rawBody.length > 64 * 1024) {
    throw new DomainApiError(400, "webhook_request_invalid");
  }
  const config = internalServiceConfig();
  const path = `/v1/webhooks/checkouts/${endpointKey}`;
  const { token } = signInternalEnvelope({
    sessionId: "appsell.webhook.ingress.v1",
    sessionEpoch: 1,
    identity: {
      issuer: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
      sub: "appsell-webhook-bff"
    },
    capability: "commerce.webhook.ingest",
    sellerOrganizationId: null,
    method: "POST",
    path,
    body: rawBody
  }, config.signing);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5_000);
  const providerHeaders: Record<string, string> = webhookHeaders.protocol === "external_checkout"
    ? {
        "x-external-checkout-webhook-event-id": webhookHeaders.eventId,
        "x-external-checkout-webhook-timestamp": webhookHeaders.timestamp,
        "x-external-checkout-webhook-signature": webhookHeaders.signature
      }
    : {
        "x-appsell-webhook-event-id": webhookHeaders.eventId,
        "x-appsell-webhook-timestamp": webhookHeaders.timestamp,
        "x-appsell-webhook-signature": webhookHeaders.signature
      };
  try {
    const response = await fetch(new URL(path, config.apiOrigin), {
      method: "POST",
      body: new Uint8Array(rawBody),
      headers: {
        "content-type": "application/json",
        "x-appsell-internal-envelope": token,
        "x-appsell-service-credential": config.serviceCredentialEncoded,
        ...providerHeaders
      },
      cache: "no-store",
      signal: controller.signal
    });
    const payload = await response.json().catch(() => null) as unknown;
    return { status: response.status, payload };
  } catch {
    throw new DomainApiError(503, "domain_api_unavailable");
  } finally {
    clearTimeout(timeout);
  }
}

function requiredEnvironment(name: string) {
  const value = process.env[name];
  if (!value) throw new DomainApiError(503, "domain_api_unavailable");
  return value;
}
