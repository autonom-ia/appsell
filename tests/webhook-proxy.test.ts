import { createHash } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  parseCheckoutWebhookHeaders,
  proxyCheckoutWebhook,
  validCheckoutWebhookEndpointKey
} from "../lib/checkout-webhook-proxy.server";

const endpointKey = "endpoint_0123456789abcdefghijklmnopqrstuv";
const key = (byte: number) => Buffer.alloc(32, byte).toString("base64");
const originalEnvironment = { ...process.env };

describe("checkout webhook proxy", () => {
  beforeEach(() => {
    process.env.APPSELL_DOMAIN_API_URL = "http://127.0.0.1:65530";
    process.env.APPSELL_INTERNAL_ISSUER = "appsell-bff";
    process.env.APPSELL_INTERNAL_AUDIENCE = "appsell-api";
    process.env.APPSELL_IDENTITY_ISSUER = "https://identity.example";
    process.env.APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON = JSON.stringify([
      { id: "internal-v1", key: key(1), active: true }
    ]);
    process.env.APPSELL_SERVICE_CREDENTIAL_B64 = key(2);
    process.env.APPSELL_SESSION_HMAC_KEY_B64 = key(3);
    process.env.APPSELL_CSRF_HMAC_KEY_B64 = key(4);
    process.env.APPSELL_VAULT_LOCAL_KEYRING_B64_JSON = "[]";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    process.env = { ...originalEnvironment };
  });

  it("validates opaque endpoint keys and exact provider headers", () => {
    expect(validCheckoutWebhookEndpointKey(endpointKey)).toBe(true);
    expect(validCheckoutWebhookEndpointKey("seller-slug")).toBe(false);
    const valid = new Headers({
      "x-appsell-webhook-event-id": "evt-2026:001",
      "x-appsell-webhook-timestamp": "1784000000",
      "x-appsell-webhook-signature": `v1=${"a".repeat(64)}`
    });
    expect(parseCheckoutWebhookHeaders(valid)).toEqual({
      protocol: "legacy",
      eventId: "evt-2026:001",
      timestamp: "1784000000",
      signature: `v1=${"a".repeat(64)}`
    });
    valid.set("x-appsell-webhook-signature", "v1=ABC");
    expect(parseCheckoutWebhookHeaders(valid)).toBeNull();
  });

  it("accepts the generic external checkout provider headers", () => {
    const valid = new Headers({
      "x-external-checkout-webhook-event-id": "financial:external-checkout:order-1",
      "x-external-checkout-webhook-timestamp": "1784000000",
      "x-external-checkout-webhook-signature": `v1=${"c".repeat(64)}`
    });
    expect(parseCheckoutWebhookHeaders(valid)).toEqual({
      protocol: "external_checkout",
      eventId: "financial:external-checkout:order-1",
      timestamp: "1784000000",
      signature: `v1=${"c".repeat(64)}`
    });
  });

  it("forwards generic provider headers with a body-bound internal envelope", async () => {
    const rawBody = Buffer.from('{ "type": "purchase.approved", "unicode": "olá" }\n');
    const fetchMock = vi.fn(async (_input: URL | RequestInfo, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(Buffer.from(init?.body as Buffer)).toEqual(rawBody);
      expect(headers.get("x-external-checkout-webhook-event-id")).toBe("evt-001");
      expect(headers.get("x-external-checkout-webhook-timestamp")).toBe("1784000000");
      expect(headers.get("x-external-checkout-webhook-signature")).toBe(`v1=${"b".repeat(64)}`);
      expect(headers.get("x-appsell-webhook-event-id")).toBeNull();
      const token = headers.get("x-appsell-internal-envelope")!;
      const encodedPayload = token.split(".")[1]!;
      const payload = JSON.parse(Buffer.from(encodedPayload, "base64url").toString("utf8"));
      expect(payload.capability).toBe("commerce.webhook.ingest");
      expect(payload.seller_organization_id).toBeNull();
      expect(payload.body_sha256).toBe(createHash("sha256").update(rawBody).digest("hex"));
      return Response.json({ status: "accepted" }, { status: 202 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(proxyCheckoutWebhook(endpointKey, rawBody, {
      protocol: "external_checkout",
      eventId: "evt-001",
      timestamp: "1784000000",
      signature: `v1=${"b".repeat(64)}`
    })).resolves.toEqual({ status: 202, payload: { status: "accepted" } });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
