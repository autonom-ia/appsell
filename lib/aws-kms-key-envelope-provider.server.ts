import "server-only";

import { createHash } from "node:crypto";
import { DecryptCommand, EncryptCommand, KMSClient } from "@aws-sdk/client-kms";

import {
  AWS_KMS_PROVIDER_ID,
  TokenVaultAuthenticationError,
  TokenVaultConfigurationError,
  TokenVaultMalformedError,
  type AwsKmsKeyEnvelope,
  type KeyEnvelope,
  type KeyEnvelopeProvider
} from "./token-vault";

const AES_KEY_BYTES = 32;
const DEFAULT_TIMEOUT_MS = 2_000;
const MIN_TIMEOUT_MS = 100;
const MAX_TIMEOUT_MS = 10_000;
const MAX_AUTHENTICATED_CONTEXT_BYTES = 8 * 1024;
// KEY_NAME_PATTERN mirrors KEY_ID_PATTERN in lib/token-vault.ts: the logical kid persisted in the envelope.
const KEY_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
// KMS key reference: key id (uuid), alias/<name>, or full ARN. Bounded, no whitespace.
const KEY_ID_REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9:/_.-]{0,2047}$/;
const REGION_PATTERN = /^[a-z]{2}-[a-z]+-\d{1,2}$/;

export interface AwsKmsConfiguration {
  keyId: string;
  keyName: string;
  region: string;
  endpoint?: string;
  timeoutMs: number;
}

export class AwsKmsKeyEnvelopeProvider implements KeyEnvelopeProvider {
  private readonly client: KMSClient;
  private readonly keyId: string;
  private readonly keyName: string;
  private readonly timeoutMs: number;

  constructor(configuration: AwsKmsConfiguration) {
    // AWS KMS is certified for production: no NODE_ENV gate here, unlike local/openbao providers.
    if (!KEY_ID_REFERENCE_PATTERN.test(configuration.keyId)) {
      throw new TokenVaultConfigurationError("AWS KMS key id is invalid.");
    }
    if (!KEY_NAME_PATTERN.test(configuration.keyName)) {
      throw new TokenVaultConfigurationError("AWS KMS key name is invalid.");
    }
    if (!REGION_PATTERN.test(configuration.region)) {
      throw new TokenVaultConfigurationError("AWS KMS region is invalid.");
    }
    if (
      !Number.isSafeInteger(configuration.timeoutMs) ||
      configuration.timeoutMs < MIN_TIMEOUT_MS ||
      configuration.timeoutMs > MAX_TIMEOUT_MS
    ) {
      throw new TokenVaultConfigurationError("AWS KMS timeout is invalid.");
    }
    const endpoint = parseEndpoint(configuration.endpoint);
    this.keyId = configuration.keyId;
    this.keyName = configuration.keyName;
    this.timeoutMs = configuration.timeoutMs;
    this.client = new KMSClient({
      region: configuration.region,
      maxAttempts: 3,
      ...(endpoint ? { endpoint } : {})
    });
  }

  static fromEnvironment(environment: Partial<Record<string, string | undefined>> = process.env) {
    const timeoutRaw = environment.APPSELL_KMS_HTTP_TIMEOUT_MS ?? String(DEFAULT_TIMEOUT_MS);
    if (!/^\d+$/.test(timeoutRaw)) {
      throw new TokenVaultConfigurationError("APPSELL_KMS_HTTP_TIMEOUT_MS is invalid.");
    }
    return new AwsKmsKeyEnvelopeProvider({
      keyId: environment.APPSELL_KMS_KEY_ID ?? "",
      keyName: environment.APPSELL_KMS_KEY_NAME ?? "",
      region: environment.APPSELL_KMS_REGION ?? "",
      endpoint: environment.APPSELL_KMS_ENDPOINT,
      timeoutMs: Number(timeoutRaw)
    });
  }

  async wrapKey(dataKey: Buffer, authenticatedContext: Buffer): Promise<AwsKmsKeyEnvelope> {
    validateDataKey(dataKey);
    validateAuthenticatedContext(authenticatedContext);
    let ciphertextBlob: Uint8Array | undefined;
    try {
      const response = await this.client.send(
        new EncryptCommand({
          KeyId: this.keyId,
          Plaintext: dataKey,
          EncryptionContext: this.encryptionContext(authenticatedContext)
        }),
        { abortSignal: AbortSignal.timeout(this.timeoutMs) }
      );
      ciphertextBlob = response.CiphertextBlob;
    } catch {
      throw new TokenVaultAuthenticationError("AWS KMS operation failed.");
    }
    if (!ciphertextBlob || ciphertextBlob.length === 0) {
      throw new TokenVaultAuthenticationError("AWS KMS operation failed.");
    }
    return {
      provider: AWS_KMS_PROVIDER_ID,
      kid: this.keyName,
      alg: "AWS_KMS",
      ciphertext: Buffer.from(ciphertextBlob).toString("base64")
    };
  }

  async unwrapKey(envelope: KeyEnvelope, authenticatedContext: Buffer): Promise<Buffer> {
    validateAuthenticatedContext(authenticatedContext);
    if (
      envelope.provider !== AWS_KMS_PROVIDER_ID ||
      envelope.alg !== "AWS_KMS" ||
      envelope.kid !== this.keyName
    ) {
      throw new TokenVaultMalformedError("Token vault key envelope provider is invalid.");
    }
    const ciphertextBlob = decodeCanonicalBase64(envelope.ciphertext);
    let plaintext: Buffer | undefined;
    try {
      const response = await this.client.send(
        new DecryptCommand({
          KeyId: this.keyId,
          CiphertextBlob: ciphertextBlob,
          EncryptionContext: this.encryptionContext(authenticatedContext)
        }),
        { abortSignal: AbortSignal.timeout(this.timeoutMs) }
      );
      plaintext = response.Plaintext ? Buffer.from(response.Plaintext) : undefined;
    } catch {
      throw new TokenVaultAuthenticationError("AWS KMS operation failed.");
    }
    if (!plaintext || plaintext.length !== AES_KEY_BYTES) {
      plaintext?.fill(0);
      throw new TokenVaultAuthenticationError("AWS KMS operation failed.");
    }
    return plaintext;
  }

  assertKeySeparation(forbiddenKeys: Buffer[]) {
    // KMS key material never leaves KMS, so in-process key collision is impossible.
    if (!Array.isArray(forbiddenKeys)) {
      throw new TokenVaultConfigurationError("Vault key separation inputs are invalid.");
    }
  }

  // The AAD is committed as a SHA-256 digest to stay within KMS EncryptionContext limits
  // while binding provider, key name and per-record context. KMS enforces equality on decrypt.
  private encryptionContext(authenticatedContext: Buffer): Record<string, string> {
    const commitment = createHash("sha256")
      .update(
        Buffer.from(
          JSON.stringify([
            "appsell-token-vault-key-provider",
            1,
            AWS_KMS_PROVIDER_ID,
            this.keyName,
            "AWS_KMS",
            authenticatedContext.toString("base64url")
          ]),
          "utf8"
        )
      )
      .digest("hex");
    return {
      p: AWS_KMS_PROVIDER_ID,
      k: this.keyName,
      ctx: commitment
    };
  }
}

function parseEndpoint(raw: string | undefined) {
  if (raw === undefined || raw === "") return undefined;
  let endpoint: URL;
  try {
    endpoint = new URL(raw);
  } catch {
    throw new TokenVaultConfigurationError("AWS KMS endpoint is invalid.");
  }
  const isLoopback =
    endpoint.hostname === "127.0.0.1" || endpoint.hostname === "localhost" || endpoint.hostname === "[::1]";
  if (
    (endpoint.protocol !== "https:" && !(endpoint.protocol === "http:" && isLoopback)) ||
    endpoint.username ||
    endpoint.password ||
    endpoint.search ||
    endpoint.hash
  ) {
    throw new TokenVaultConfigurationError("AWS KMS endpoint is invalid.");
  }
  return endpoint.origin;
}

function validateDataKey(value: Buffer) {
  if (!Buffer.isBuffer(value) || value.length !== AES_KEY_BYTES) {
    throw new TokenVaultAuthenticationError("Token vault data key is invalid.");
  }
}

function validateAuthenticatedContext(value: Buffer) {
  if (!Buffer.isBuffer(value) || value.length === 0 || value.length > MAX_AUTHENTICATED_CONTEXT_BYTES) {
    throw new TokenVaultAuthenticationError("Token vault authenticated context is invalid.");
  }
}

function decodeCanonicalBase64(value: string) {
  const decoded = Buffer.from(value, "base64");
  if (decoded.length === 0 || decoded.toString("base64") !== value) {
    decoded.fill(0);
    throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
  }
  return decoded;
}
