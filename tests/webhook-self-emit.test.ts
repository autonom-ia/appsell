import { createHash, createHmac } from "node:crypto";

import { expect, test } from "vitest";

import {
  SelfEmitConfigError,
  SelfEmitPayloadError,
  buildCheckoutApprovedPayload,
  selfEmitEventId,
  selfEmitWebhookConfig,
  serializeCheckoutPayload,
  signSelfEmitWebhook
} from "../lib/webhook-self-emit.server";

const secret = Buffer.from("01234567890123456789012345678901");
const secretB64 = secret.toString("base64");
const endpointKey = "endpoint_0123456789abcdefghijklmnopqrstuv";

test("selfEmitEventId is deterministic per order id (idempotent replay key)", () => {
  expect(selfEmitEventId("order-123")).toBe("selfemit:order-123");
  expect(selfEmitEventId("order-123")).toBe(selfEmitEventId("order-123"));
  expect(selfEmitEventId("order-abc")).not.toBe(selfEmitEventId("order-123"));
});

test("builds a v2 payload with exactly six keys, normalized e-mail and Z-suffixed occurred_at", () => {
  const payload = buildCheckoutApprovedPayload({
    financialProductKey: "prod_codex_v1",
    orderId: "order-123",
    buyerEmail: "  Buyer@Example.TEST  ",
    buyerIdentitySub: "auth0|abc123",
    occurredAtIso: "2026-07-21T10:00:00.000Z"
  });

  expect(Object.keys(payload).sort()).toEqual([
    "buyer_email",
    "buyer_identity_sub",
    "event_type",
    "external_product_id",
    "external_purchase_id",
    "occurred_at"
  ].sort());
  expect(payload).toEqual({
    event_type: "purchase.approved",
    external_product_id: "prod_codex_v1",
    external_purchase_id: "order-123",
    occurred_at: "2026-07-21T10:00:00.000Z",
    buyer_email: "buyer@example.test",
    buyer_identity_sub: "auth0|abc123"
  });
});

test("accepts a null buyer_identity_sub for anonymous buyers", () => {
  const payload = buildCheckoutApprovedPayload({
    financialProductKey: "prod_codex_v1",
    orderId: "order-123",
    buyerEmail: "buyer@example.test",
    buyerIdentitySub: null,
    occurredAtIso: "2026-07-21T10:00:00.000Z"
  });
  expect(payload.buyer_identity_sub).toBeNull();
});

test("rejects a buyer_identity_sub belonging to an anonymous registration-checkout subject", () => {
  expect(() => buildCheckoutApprovedPayload({
    financialProductKey: "prod_codex_v1",
    orderId: "order-123",
    buyerEmail: "buyer@example.test",
    buyerIdentitySub: "registration:some-anonymous-handle",
    occurredAtIso: "2026-07-21T10:00:00.000Z"
  })).toThrow(SelfEmitPayloadError);
});

test("rejects an invalid buyer e-mail, unsafe references, or an unparsable occurred_at", () => {
  const base = {
    financialProductKey: "prod_codex_v1",
    orderId: "order-123",
    buyerEmail: "buyer@example.test",
    buyerIdentitySub: null,
    occurredAtIso: "2026-07-21T10:00:00.000Z"
  };
  expect(() => buildCheckoutApprovedPayload({ ...base, buyerEmail: "not-an-email" })).toThrow(SelfEmitPayloadError);
  expect(() => buildCheckoutApprovedPayload({ ...base, financialProductKey: "has space" })).toThrow(SelfEmitPayloadError);
  expect(() => buildCheckoutApprovedPayload({ ...base, orderId: "" })).toThrow(SelfEmitPayloadError);
  expect(() => buildCheckoutApprovedPayload({ ...base, occurredAtIso: "not-a-date" })).toThrow(SelfEmitPayloadError);
});

test("signature is verifiable against an independent HMAC computation of the same canonical string", () => {
  const payload = buildCheckoutApprovedPayload({
    financialProductKey: "prod_codex_v1",
    orderId: "order-123",
    buyerEmail: "buyer@example.test",
    buyerIdentitySub: null,
    occurredAtIso: "2026-07-21T10:00:00.000Z"
  });
  const body = serializeCheckoutPayload(payload);
  const eventId = selfEmitEventId("order-123");
  const timestampSeconds = 1_753_000_000;
  const headers = signSelfEmitWebhook({ endpointKey, eventId, timestampSeconds, body, secret });

  expect(headers.eventId).toBe(eventId);
  expect(headers.timestamp).toBe(String(timestampSeconds));
  expect(headers.signature).toMatch(/^v1=[0-9a-f]{64}$/);

  const payloadHash = createHash("sha256").update(body).digest("hex");
  const canonical = [
    "appsell.webhook.generic-hmac.v1",
    endpointKey,
    String(timestampSeconds),
    eventId,
    payloadHash
  ].join("\n");
  const expectedSignature = `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`;
  expect(headers.signature).toBe(expectedSignature);
});

test("two signing calls for the identical payload/order produce the same payload hash (idempotent replay-safe)", () => {
  const buildBody = () => serializeCheckoutPayload(buildCheckoutApprovedPayload({
    financialProductKey: "prod_codex_v1",
    orderId: "order-123",
    buyerEmail: "buyer@example.test",
    buyerIdentitySub: null,
    occurredAtIso: "2026-07-21T10:00:00.000Z"
  }));
  const first = signSelfEmitWebhook({
    endpointKey,
    eventId: selfEmitEventId("order-123"),
    timestampSeconds: 1_753_000_000,
    body: buildBody(),
    secret
  });
  // A later retry naturally has a different wall-clock timestamp...
  const second = signSelfEmitWebhook({
    endpointKey,
    eventId: selfEmitEventId("order-123"),
    timestampSeconds: 1_753_000_050,
    body: buildBody(),
    secret
  });
  expect(second.eventId).toBe(first.eventId);
  // ...but because occurred_at comes from the order's own paid_at (never
  // wall-clock "now"), the two payload bodies are byte-identical, so the
  // domain webhook processor sees the same payload_sha256 for the same event
  // id and treats the retry as a replay instead of a conflicting event.
  expect(buildBody().equals(buildBody())).toBe(true);
  expect(first.signature).not.toBe(second.signature);
});

test("rejects a secret that is not exactly 32 bytes", () => {
  const body = Buffer.from("{}");
  expect(() => signSelfEmitWebhook({
    endpointKey,
    eventId: "selfemit:order-123",
    timestampSeconds: 1_753_000_000,
    body,
    secret: Buffer.from("short")
  })).toThrow(SelfEmitConfigError);
});

test("selfEmitWebhookConfig validates the endpoint key and base64-32-byte secret", () => {
  const config = selfEmitWebhookConfig({
    APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY: endpointKey,
    APPSELL_CHECKOUT_WEBHOOK_SECRET_B64: secretB64
  });
  expect(config.endpointKey).toBe(endpointKey);
  expect(config.secret.equals(secret)).toBe(true);

  expect(() => selfEmitWebhookConfig({
    APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY: "too-short",
    APPSELL_CHECKOUT_WEBHOOK_SECRET_B64: secretB64
  })).toThrow(SelfEmitConfigError);

  expect(() => selfEmitWebhookConfig({
    APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY: endpointKey,
    APPSELL_CHECKOUT_WEBHOOK_SECRET_B64: Buffer.from("short").toString("base64")
  })).toThrow(SelfEmitConfigError);

  expect(() => selfEmitWebhookConfig({})).toThrow(SelfEmitConfigError);
});
