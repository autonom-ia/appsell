import { randomBytes } from "node:crypto";

import { describe, expect, it } from "vitest";

import {
  InternalEnvelopeConfigurationError,
  InternalEnvelopeInputError,
  parseInternalSigningConfig,
  signInternalEnvelope
} from "../lib/internal-envelope";

const fixedKey = Buffer.from(Array.from({ length: 32 }, (_, index) => index));
const config = {
  issuer: "appsell-bff",
  audience: "appsell-domain-api",
  signingKey: { id: "local-v1", key: fixedKey }
};
const input = {
  sessionId: "opaque-session-001",
  sessionEpoch: 1,
  identity: {
    issuer: "https://identity.autonomia.site",
    sub: "github|12345"
  },
  capability: "seller.onboard",
  sellerOrganizationId: null,
  method: "POST",
  path: "/v1/me/sellers",
  body: Buffer.from('{"slug":"studio-x","display_name":"Studio X"}', "utf8"),
  requestId: "018f47c2-4d4f-4d5b-8c6a-0f3d9ec6bc14",
  nowSeconds: 1_800_000_000,
  ttlSeconds: 30
};

describe("internal BFF envelope", () => {
  it("matches the cross-runtime golden vector", () => {
    const signed = signInternalEnvelope(input, config);
    expect(signed.token).toBe("v1.eyJ2ZXJzaW9uIjoxLCJraWQiOiJsb2NhbC12MSIsImlzc3VlciI6ImFwcHNlbGwtYmZmIiwiYXVkaWVuY2UiOiJhcHBzZWxsLWRvbWFpbi1hcGkiLCJzZXNzaW9uX3JlZiI6Ijg0NTRjMzg1ZjEzOTEzYjcxMTIwYWRiZmQxM2VhMTc0OTI0MTIwYmQ4MDc0ZGZjOGEyOWFmNWM3YWE5ZTk3NWUiLCJzZXNzaW9uX2Vwb2NoIjoxLCJpZGVudGl0eSI6eyJpc3N1ZXIiOiJodHRwczovL2lkZW50aXR5LmF1dG9ub21pYS5zaXRlIiwic3ViIjoiZ2l0aHVifDEyMzQ1IiwidXNlcl9pZCI6IjE5MjA5OGVjLTBkOGEtNWM2OC05NjA2LWJjZWU0MmVjOTRkNyJ9LCJjYXBhYmlsaXR5Ijoic2VsbGVyLm9uYm9hcmQiLCJzZWxsZXJfb3JnYW5pemF0aW9uX2lkIjpudWxsLCJtZXRob2QiOiJQT1NUIiwicGF0aCI6Ii92MS9tZS9zZWxsZXJzIiwicXVlcnlfc2hhMjU2IjoiZTNiMGM0NDI5OGZjMWMxNDlhZmJmNGM4OTk2ZmI5MjQyN2FlNDFlNDY0OWI5MzRjYTQ5NTk5MWI3ODUyYjg1NSIsImJvZHlfc2hhMjU2IjoiYjcwY2YyMTEyMTUzYWVlNDRiOWNmMGI5OTdmM2E1OGIwMjc2ODQ2MDY3ZjYwMGRiYzBiMGFlOTg5MjMzYzBkYyIsInJlcXVlc3RfaWQiOiIwMThmNDdjMi00ZDRmLTRkNWItOGM2YS0wZjNkOWVjNmJjMTQiLCJpYXQiOjE4MDAwMDAwMDAsImV4cCI6MTgwMDAwMDAzMH0.Y0nQ2f4mSXqCL5Adijdmt0mV1SIWDUJ6YjuhX8JZKh8");
    expect(signed.payload.identity.user_id).toBe("192098ec-0d8a-5c68-9606-bcee42ec94d7");
    expect(signed.payload.seller_organization_id).toBeNull();
  });

  it("binds method, route, body, capability, identity and session", () => {
    const original = signInternalEnvelope(input, config);
    for (const change of [
      { method: "PUT" },
      { path: "/v1/me" },
      { query: Buffer.from("page=2") },
      { body: Buffer.from("{}") },
      { capability: "identity.read" },
      { sessionId: "opaque-session-002" },
      { identity: { ...input.identity, sub: "github|67890" } }
    ]) {
      expect(signInternalEnvelope({ ...input, ...change }, config).token).not.toBe(original.token);
    }
  });

  it("rejects invalid TTLs, routes and identifiers", () => {
    expect(() => signInternalEnvelope({ ...input, ttlSeconds: 61 }, config)).toThrow(InternalEnvelopeInputError);
    expect(() => signInternalEnvelope({ ...input, path: "//v1/me" }, config)).toThrow(InternalEnvelopeInputError);
    const externalCheckoutWebhookPath = "/v1/webhooks/checkouts/AbCdEFghIjKlMNopQRstUVwxYZ0123456789-_aBc";
    expect(signInternalEnvelope({
      ...input,
      capability: "commerce.webhook.ingest",
      path: externalCheckoutWebhookPath
    }, config).payload.path).toBe(externalCheckoutWebhookPath);
    const curriculumPath = "/v1/me/sellers/52000000-0000-4000-8000-000000000001/products/53000000-0000-4000-8000-000000000001/courses/54000000-0000-4000-8000-000000000001/modules/55000000-0000-4000-8000-000000000001/lessons";
    expect(signInternalEnvelope({ ...input, path: curriculumPath }, config).payload.path).toBe(curriculumPath);
    expect(() => signInternalEnvelope({ ...input, path: `/${"a".repeat(512)}` }, config)).toThrow(InternalEnvelopeInputError);
    expect(() => signInternalEnvelope({ ...input, requestId: "not-a-uuid" }, config)).toThrow(InternalEnvelopeInputError);
    for (const sub of [" ab", "ab ", "a\nb", "a\u0085b", "\ud800ab", "\udc00ab", "ab", "x".repeat(256)]) {
      expect(() => signInternalEnvelope({
        ...input,
        identity: { ...input.identity, sub }
      }, config)).toThrow(InternalEnvelopeInputError);
    }
    expect(() => signInternalEnvelope({
      ...input,
      identity: { ...input.identity, sub: "\u00a0subject" }
    }, config)).not.toThrow();
  });

  it("requires one active canonical independent key", () => {
    const environment = {
      APPSELL_INTERNAL_ISSUER: "appsell-bff",
      APPSELL_INTERNAL_AUDIENCE: "appsell-domain-api",
      APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: JSON.stringify([
        { id: "local-v1", key: fixedKey.toString("base64"), active: true }
      ])
    };
    expect(parseInternalSigningConfig(environment, []).signingKey.id).toBe("local-v1");
    expect(() => parseInternalSigningConfig(environment, [fixedKey])).toThrow(InternalEnvelopeConfigurationError);
    expect(() => parseInternalSigningConfig({
      ...environment,
      APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: JSON.stringify([
        { id: "a", key: randomBytes(32).toString("base64"), active: true },
        { id: "b", key: randomBytes(32).toString("base64"), active: true }
      ])
    }, [])).toThrow(InternalEnvelopeConfigurationError);
  });
});
