import { describe, expect, it } from "vitest";

import { sha256 } from "../lib/crypto";
import {
  EnvelopeTokenVault,
  LocalAesKeyEnvelopeProvider,
  TokenVaultAuthenticationError,
  TokenVaultConfigurationError,
  TokenVaultMalformedError,
  TokenVaultUnknownKeyError,
  type KeyEnvelope,
  type KeyEnvelopeProvider,
  type LocalKeyEntry,
  type TokenBundle,
  type TokenVaultContext
} from "../lib/token-vault";

const firstKey = Buffer.alloc(32, 11);
const secondKey = Buffer.alloc(32, 22);
const sessionHmacKey = Buffer.alloc(32, 33);

const context: TokenVaultContext = {
  namespace: "appsell:test:vault-tests",
  sessionHash: sha256("session-one"),
  subject: "identity-user-001"
};

const bundle: TokenBundle = {
  subject: context.subject,
  tokenType: "Bearer",
  accessToken: "access-token-plaintext",
  refreshToken: "refresh-token-plaintext",
  accessTokenExpiresAt: 2_000_000_000_000,
  refreshTokenExpiresAt: 2_000_003_600_000,
  scopes: ["openid", "profile"],
  revision: 1
};

function vault(entries: LocalKeyEntry[]) {
  return new EnvelopeTokenVault(new LocalAesKeyEnvelopeProvider(entries, {
    nodeEnvironment: "test",
    forbiddenKeys: [sessionHmacKey]
  }));
}

describe("token vault envelope encryption", () => {
  it("round-trips a closed bundle without serializing plaintext or deterministic ciphertext", async () => {
    const tokenVault = vault([{ id: "local-v1", key: firstKey, active: true }]);
    const first = await tokenVault.seal(bundle, context);
    const second = await tokenVault.seal(bundle, context);

    expect(first).not.toBe(second);
    expect(first).not.toContain(bundle.subject);
    expect(first).not.toContain(bundle.accessToken);
    expect(first).not.toContain(bundle.refreshToken);
    expect(await tokenVault.open(first, context)).toEqual(bundle);
    expect(await tokenVault.open(second, context)).toEqual(bundle);
  });

  it("fails authentication for payload/key tamper and every authenticated context dimension", async () => {
    const tokenVault = vault([{ id: "local-v1", key: firstKey, active: true }]);
    const serialized = await tokenVault.seal(bundle, context);
    const payloadTamper = mutateEnvelope(serialized, ["payload", "ct"]);
    const payloadTagTamper = mutateEnvelope(serialized, ["payload", "tag"]);
    const wrappedKeyTamper = mutateEnvelope(serialized, ["keyEnvelope", "ct"]);
    const wrappedKeyTagTamper = mutateEnvelope(serialized, ["keyEnvelope", "tag"]);

    await expect(tokenVault.open(payloadTamper, context)).rejects.toBeInstanceOf(TokenVaultAuthenticationError);
    await expect(tokenVault.open(payloadTagTamper, context)).rejects.toBeInstanceOf(TokenVaultAuthenticationError);
    await expect(tokenVault.open(wrappedKeyTamper, context)).rejects.toBeInstanceOf(TokenVaultAuthenticationError);
    await expect(tokenVault.open(wrappedKeyTagTamper, context)).rejects.toBeInstanceOf(TokenVaultAuthenticationError);
    const wrongKeyVault = vault([{ id: "local-v1", key: secondKey, active: true }]);
    await expect(wrongKeyVault.open(serialized, context)).rejects.toBeInstanceOf(TokenVaultAuthenticationError);
    await expect(tokenVault.open(serialized, { ...context, sessionHash: sha256("session-two") }))
      .rejects.toBeInstanceOf(TokenVaultAuthenticationError);
    await expect(tokenVault.open(serialized, { ...context, subject: "identity-user-002" }))
      .rejects.toBeInstanceOf(TokenVaultAuthenticationError);
    await expect(tokenVault.open(serialized, { ...context, namespace: "appsell:test:other-tests" }))
      .rejects.toBeInstanceOf(TokenVaultAuthenticationError);
  });

  it("writes only with the active key and reads an older key explicitly during rotation", async () => {
    const oldVault = vault([{ id: "local-v1", key: firstKey, active: true }]);
    const oldEnvelope = await oldVault.seal(bundle, context);
    expect(JSON.parse(oldEnvelope).keyEnvelope.kid).toBe("local-v1");

    const rotatingVault = vault([
      { id: "local-v1", key: firstKey, active: false },
      { id: "local-v2", key: secondKey, active: true }
    ]);
    expect(await rotatingVault.open(oldEnvelope, context)).toEqual(bundle);
    const newEnvelope = await rotatingVault.seal(bundle, context);
    expect(JSON.parse(newEnvelope).keyEnvelope.kid).toBe("local-v2");

    await expect(oldVault.open(newEnvelope, context)).rejects.toBeInstanceOf(TokenVaultUnknownKeyError);

    const tampered = JSON.parse(oldEnvelope);
    tampered.keyEnvelope.kid = "local-v2";
    await expect(rotatingVault.open(JSON.stringify(tampered), context))
      .rejects.toBeInstanceOf(TokenVaultAuthenticationError);
  });

  it("rejects unknown schema, algorithm, fields and non-canonical AEAD material", async () => {
    const tokenVault = vault([{ id: "local-v1", key: firstKey, active: true }]);
    const serialized = await tokenVault.seal(bundle, context);
    const envelope = JSON.parse(serialized);

    await expect(tokenVault.open(JSON.stringify({ ...envelope, v: 2 }), context))
      .rejects.toBeInstanceOf(TokenVaultMalformedError);
    await expect(tokenVault.open(JSON.stringify({ ...envelope, alg: "none" }), context))
      .rejects.toBeInstanceOf(TokenVaultMalformedError);
    await expect(tokenVault.open(JSON.stringify({ ...envelope, unexpected: true }), context))
      .rejects.toBeInstanceOf(TokenVaultMalformedError);
    await expect(tokenVault.open(JSON.stringify({ ...envelope, payload: { ...envelope.payload, iv: "not+base64" } }), context))
      .rejects.toBeInstanceOf(TokenVaultMalformedError);
    await expect(tokenVault.open(JSON.stringify({ ...envelope, payload: { ...envelope.payload, tag: "AA" } }), context))
      .rejects.toBeInstanceOf(TokenVaultMalformedError);
  });

  it("rejects bundles too large to reopen and malformed provider output before persistence", async () => {
    const tokenVault = vault([{ id: "local-v1", key: firstKey, active: true }]);
    await expect(tokenVault.seal({
      ...bundle,
      accessToken: "a".repeat(16 * 1024),
      refreshToken: "r".repeat(16 * 1024)
    }, context)).rejects.toBeInstanceOf(TokenVaultMalformedError);

    const malformedProvider: KeyEnvelopeProvider = {
      async wrapKey() {
        return {
          provider: "p".repeat(65),
          kid: "bad kid",
          alg: "A256GCM",
          iv: "AA",
          ct: "AA",
          tag: "AA",
          leaked: "unexpected"
        } as unknown as KeyEnvelope;
      },
      async unwrapKey() {
        return Buffer.alloc(32);
      },
      assertKeySeparation() {
        // No local key material in this malformed provider fixture.
      }
    };
    const malformedVault = new EnvelopeTokenVault(malformedProvider);
    await expect(malformedVault.seal(bundle, context)).rejects.toBeInstanceOf(TokenVaultMalformedError);
  });

  it("rejects invalid keyrings, HMAC reuse and the local provider in production", () => {
    expect(() => new LocalAesKeyEnvelopeProvider([], { nodeEnvironment: "test" }))
      .toThrow(TokenVaultConfigurationError);
    expect(() => new LocalAesKeyEnvelopeProvider([
      { id: "one", key: firstKey, active: false },
      { id: "two", key: secondKey, active: false }
    ], { nodeEnvironment: "test" })).toThrow("exactly one active");
    expect(() => new LocalAesKeyEnvelopeProvider([
      { id: "one", key: firstKey, active: true },
      { id: "two", key: secondKey, active: true }
    ], { nodeEnvironment: "test" })).toThrow("exactly one active");
    expect(() => new LocalAesKeyEnvelopeProvider([
      { id: "same", key: firstKey, active: true },
      { id: "same", key: secondKey, active: false }
    ], { nodeEnvironment: "test" })).toThrow("unique");
    expect(() => new LocalAesKeyEnvelopeProvider([
      { id: "one", key: firstKey, active: true },
      { id: "two", key: Buffer.from(firstKey), active: false }
    ], { nodeEnvironment: "test" })).toThrow("material must be unique");
    expect(() => new LocalAesKeyEnvelopeProvider([
      { id: "short", key: Buffer.alloc(31), active: true }
    ], { nodeEnvironment: "test" })).toThrow("32 bytes");
    expect(() => new LocalAesKeyEnvelopeProvider([
      { id: "reused", key: sessionHmacKey, active: true }
    ], { nodeEnvironment: "test", forbiddenKeys: [sessionHmacKey] })).toThrow("distinct");
    expect(() => new LocalAesKeyEnvelopeProvider([
      { id: "prod", key: firstKey, active: true }
    ], { nodeEnvironment: "production" })).toThrow("forbidden in production");
  });

  it("parses only canonical environment keyrings and blocks production composition", () => {
    const keyring = JSON.stringify([{ id: "local-v1", key: firstKey.toString("base64"), active: true }]);
    expect(LocalAesKeyEnvelopeProvider.fromEnvironment({
      NODE_ENV: "test",
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: keyring
    }, [sessionHmacKey])).toBeInstanceOf(LocalAesKeyEnvelopeProvider);
    expect(() => LocalAesKeyEnvelopeProvider.fromEnvironment({
      NODE_ENV: "test",
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
        { id: "local-v1", key: firstKey.toString("base64url"), active: true }
      ])
    })).toThrow("Local vault key is invalid");
    expect(() => LocalAesKeyEnvelopeProvider.fromEnvironment({
      NODE_ENV: "production",
      APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: keyring
    })).toThrow("forbidden in production");

    const originalNodeEnvironment = process.env.NODE_ENV;
    try {
      Object.defineProperty(process.env, "NODE_ENV", {
        value: "production",
        configurable: true,
        enumerable: true,
        writable: true
      });
      expect(() => new LocalAesKeyEnvelopeProvider([
        { id: "override", key: firstKey, active: true }
      ], { nodeEnvironment: "test" })).toThrow("forbidden in production");
      expect(() => LocalAesKeyEnvelopeProvider.fromEnvironment({
        NODE_ENV: "test",
        APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: keyring
      })).toThrow("forbidden in production");
    } finally {
      if (originalNodeEnvironment === undefined) Reflect.deleteProperty(process.env, "NODE_ENV");
      else Object.defineProperty(process.env, "NODE_ENV", {
        value: originalNodeEnvironment,
        configurable: true,
        enumerable: true,
        writable: true
      });
    }
  });
});

function mutateEnvelope(serialized: string, path: ["payload" | "keyEnvelope", "ct" | "tag"]) {
  const envelope = JSON.parse(serialized);
  const current = envelope[path[0]][path[1]] as string;
  envelope[path[0]][path[1]] = `${current.startsWith("A") ? "B" : "A"}${current.slice(1)}`;
  return JSON.stringify(envelope);
}
