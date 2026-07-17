import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { OpenBaoTransitKeyEnvelopeProvider } from "../lib/openbao-transit-key-envelope-provider.server";
import { createTokenVaultFromEnvironment } from "../lib/token-vault-provider.server";
import {
  OPENBAO_TRANSIT_PROVIDER_ID,
  TokenVaultAuthenticationError,
  TokenVaultConfigurationError,
  TokenVaultMalformedError,
  type KeyEnvelope
} from "../lib/token-vault";

const opaqueCiphertext = Buffer.alloc(32, 7).toString("base64");
const environment = {
  NODE_ENV: "test",
  APPSELL_OPENBAO_ADDR: "http://127.0.0.1:58200",
  APPSELL_OPENBAO_TRANSIT_MOUNT: "appsell-transit",
  APPSELL_OPENBAO_TRANSIT_KEY: "appsell-oauth-vault",
  APPSELL_OPENBAO_TOKEN: "runtime-token-with-minimum-length",
  APPSELL_OPENBAO_HTTP_TIMEOUT_MS: "1000"
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OpenBao Transit key envelope provider", () => {
  it("wraps and unwraps a 32-byte data key with bounded server-only requests", async () => {
    const dataKey = Buffer.alloc(32, 41);
    const context = Buffer.from("authenticated-appsell-context", "utf8");
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (url.includes("/encrypt/")) {
        return Response.json({ data: { ciphertext: `vault:v2:${opaqueCiphertext}` } });
      }
      return Response.json({ data: { plaintext: dataKey.toString("base64") } });
    }));

    const provider = OpenBaoTransitKeyEnvelopeProvider.fromEnvironment(environment);
    const envelope = await provider.wrapKey(dataKey, context);
    expect(envelope).toEqual({
      provider: OPENBAO_TRANSIT_PROVIDER_ID,
      kid: "appsell-oauth-vault",
      alg: "OPENBAO_TRANSIT",
      ciphertext: `vault:v2:${opaqueCiphertext}`,
      keyVersion: 2
    });
    expect(await provider.unwrapKey(envelope, context)).toEqual(dataKey);
    expect(calls).toHaveLength(2);
    expect(calls[0]!.url).toBe("http://127.0.0.1:58200/v1/appsell-transit/encrypt/appsell-oauth-vault");
    expect(calls[1]!.url).toBe("http://127.0.0.1:58200/v1/appsell-transit/decrypt/appsell-oauth-vault");
    for (const call of calls) {
      expect(call.init).toMatchObject({ method: "POST", cache: "no-store", credentials: "omit", redirect: "manual" });
      expect((call.init.headers as Record<string, string>)["x-vault-token"]).toBe(environment.APPSELL_OPENBAO_TOKEN);
      expect(call.init.body).not.toContain(environment.APPSELL_OPENBAO_TOKEN);
    }
  });

  it("rejects mismatched providers, key names, versions, and malformed ciphertext without fallback", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const provider = OpenBaoTransitKeyEnvelopeProvider.fromEnvironment(environment);
    const valid = {
      provider: OPENBAO_TRANSIT_PROVIDER_ID,
      kid: "appsell-oauth-vault",
      alg: "OPENBAO_TRANSIT",
      ciphertext: `vault:v1:${opaqueCiphertext}`,
      keyVersion: 1
    } satisfies KeyEnvelope;
    const invalid: KeyEnvelope[] = [
      { ...valid, provider: "local-aes-gcm-v1" } as unknown as KeyEnvelope,
      { ...valid, kid: "other-key" },
      { ...valid, keyVersion: 2 },
      { ...valid, ciphertext: "plaintext" },
      { ...valid, ciphertext: "vault:v1:A" },
      { ...valid, ciphertext: "vault:v1:AA=" }
    ];
    for (const envelope of invalid) {
      await expect(provider.unwrapKey(envelope, Buffer.from("context"))).rejects.toBeInstanceOf(TokenVaultMalformedError);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed with sanitized errors on HTTP, protocol, and transport failures", async () => {
    const provider = OpenBaoTransitKeyEnvelopeProvider.fromEnvironment(environment);
    for (const failure of [
      async () => new Response(JSON.stringify({ errors: ["secret detail"] }), { status: 403 }),
      async () => Response.json({ data: { ciphertext: "not-a-vault-ciphertext" } }),
      async () => { throw new Error(`leaked-${environment.APPSELL_OPENBAO_TOKEN}`); }
    ]) {
      vi.stubGlobal("fetch", vi.fn(failure));
      const operation = provider.wrapKey(Buffer.alloc(32, 5), Buffer.from("context"));
      await expect(operation).rejects.toBeInstanceOf(TokenVaultAuthenticationError);
      await expect(operation).rejects.not.toThrow(environment.APPSELL_OPENBAO_TOKEN);
    }
  });

  it("strictly validates Local/CI configuration and rejects production", () => {
    expect(() => OpenBaoTransitKeyEnvelopeProvider.fromEnvironment({ ...environment, NODE_ENV: "production" }))
      .toThrow(TokenVaultConfigurationError);
    expect(() => OpenBaoTransitKeyEnvelopeProvider.fromEnvironment({
      ...environment,
      APPSELL_OPENBAO_ADDR: "http://openbao.internal:8200"
    })).toThrow(TokenVaultConfigurationError);
    expect(() => OpenBaoTransitKeyEnvelopeProvider.fromEnvironment({
      ...environment,
      APPSELL_OPENBAO_TOKEN: "short"
    })).toThrow(TokenVaultConfigurationError);
    expect(() => OpenBaoTransitKeyEnvelopeProvider.fromEnvironment({
      ...environment,
      APPSELL_OPENBAO_HTTP_TIMEOUT_MS: "99"
    })).toThrow(TokenVaultConfigurationError);
  });

  it("selects providers explicitly while preserving the legacy local format", () => {
    const localKey = Buffer.alloc(32, 72).toString("base64");
    expect(createTokenVaultFromEnvironment({
      NODE_ENV: "test",
      APPSELL_VAULT_PROVIDER: "local",
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([{ id: "local-v1", key: localKey, active: true }])
    })).toBeDefined();
    expect(createTokenVaultFromEnvironment({
      ...environment,
      APPSELL_VAULT_PROVIDER: "openbao-transit"
    })).toBeDefined();
    expect(() => createTokenVaultFromEnvironment({ ...environment, APPSELL_VAULT_PROVIDER: "unknown" }))
      .toThrow(TokenVaultConfigurationError);
  });
});
