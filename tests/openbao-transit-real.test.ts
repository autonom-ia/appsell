import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { OpenBaoTransitKeyEnvelopeProvider } from "../lib/openbao-transit-key-envelope-provider.server";
import { EnvelopeTokenVault, TokenVaultAuthenticationError, TokenVaultMalformedError } from "../lib/token-vault";

const enabled = process.env.APPSELL_OPENBAO_REAL_TEST === "1";
const realTest = enabled ? it : it.skip;

describe("real disposable OpenBao Transit", () => {
  realTest("wraps, rotates, migrates through the provider, and preserves N-1 without exposing key material", async () => {
    const address = required("APPSELL_OPENBAO_ADDR");
    const mount = required("APPSELL_OPENBAO_TRANSIT_MOUNT");
    const keyName = required("APPSELL_OPENBAO_TRANSIT_KEY");
    const runtimeToken = required("APPSELL_OPENBAO_TOKEN");
    const rootToken = required("APPSELL_OPENBAO_TEST_ROOT_TOKEN");
    const provider = OpenBaoTransitKeyEnvelopeProvider.fromEnvironment(process.env);
    const context = Buffer.from("real-openbao-authenticated-context", "utf8");
    const dataKey = Buffer.alloc(32, 93);

    const keyMetadata = await rootRequest(address, rootToken, `v1/${mount}/keys/${keyName}`, "GET");
    expect(keyMetadata.data).toMatchObject({
      type: "aes256-gcm96",
      exportable: false,
      allow_plaintext_backup: false,
      latest_version: 1,
      min_decryption_version: 1
    });
    const tokenMetadata = await rootRequest(address, rootToken, "v1/auth/token/lookup", "POST", {
      token: runtimeToken
    });
    expect(tokenMetadata.data).toMatchObject({
      policies: ["appsell-transit-runtime"],
      orphan: true,
      renewable: false
    });
    expect(Number(tokenMetadata.data?.ttl)).toBeGreaterThan(0);
    expect(Number(tokenMetadata.data?.ttl)).toBeLessThanOrEqual(900);

    const first = await provider.wrapKey(dataKey, context);
    expect(first.keyVersion).toBe(1);
    expect(await provider.unwrapKey(first, context)).toEqual(dataKey);
    expect(JSON.stringify(first)).not.toContain(dataKey.toString("base64"));
    expect(JSON.stringify(first)).not.toContain(context.toString("utf8"));

    const forbiddenRead = await fetch(`${address}/v1/${mount}/keys/${keyName}`, {
      headers: { "x-vault-token": runtimeToken },
      redirect: "manual"
    });
    expect(forbiddenRead.status).toBe(403);

    await rootRequest(address, rootToken, `v1/${mount}/keys/${keyName}/rotate`, "POST", {});
    const second = await provider.wrapKey(dataKey, context);
    expect(second.keyVersion).toBe(2);
    expect(await provider.unwrapKey(first, context)).toEqual(dataKey);
    expect(await provider.unwrapKey(second, context)).toEqual(dataKey);

    const migrationKey = await provider.unwrapKey(first, context);
    let migrated: Awaited<ReturnType<OpenBaoTransitKeyEnvelopeProvider["wrapKey"]>>;
    try {
      migrated = await provider.wrapKey(migrationKey, context);
    } finally {
      migrationKey.fill(0);
    }
    expect(migrated.keyVersion).toBe(2);
    expect(await provider.unwrapKey(migrated, context)).toEqual(dataKey);

    await expect(provider.unwrapKey({ ...second, keyVersion: 1 }, context))
      .rejects.toBeInstanceOf(TokenVaultMalformedError);
    await expect(provider.unwrapKey(second, Buffer.from("wrong-context")))
      .rejects.toBeInstanceOf(TokenVaultAuthenticationError);

    const tokenVault = new EnvelopeTokenVault(provider);
    const vaultContext = {
      namespace: "appsell:test:openbao-real",
      sessionHash: "A".repeat(43),
      subject: "identity-user-001",
      revision: 1
    };
    const sealed = await tokenVault.seal({
      subject: vaultContext.subject,
      tokenType: "Bearer",
      accessToken: "verified-access-token",
      refreshToken: "r".repeat(43),
      accessTokenExpiresAt: Date.now() + 60_000,
      refreshTokenExpiresAt: Date.now() + 300_000,
      scopes: ["email", "openid", "profile"],
      revision: 1,
      identity: {
        issuer: "https://identity.example.test",
        email: "buyer.demo@autonomia.site",
        emailVerified: true
      }
    }, vaultContext);
    expect(sealed).not.toContain("verified-access-token");
    expect(sealed).not.toContain("buyer.demo@autonomia.site");
    expect(await tokenVault.open(sealed, vaultContext)).toMatchObject({ subject: vaultContext.subject });
  }, 15_000);
});

function required(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required for the disposable OpenBao test.`);
  return value;
}

async function rootRequest(
  address: string,
  token: string,
  path: string,
  method: "GET" | "POST",
  body?: Record<string, unknown>
): Promise<{ data?: Record<string, unknown> }> {
  const response = await fetch(`${address}/${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      "x-vault-token": token
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    redirect: "manual"
  });
  const payload = await response.json() as { data?: Record<string, unknown>; errors?: string[] };
  if (!response.ok) throw new Error(`Disposable OpenBao bootstrap request failed with ${response.status}.`);
  return payload;
}
