import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { DecryptCommand, EncryptCommand, KMSClient } from "@aws-sdk/client-kms";
import { mockClient } from "aws-sdk-client-mock";

import { AwsKmsKeyEnvelopeProvider } from "../lib/aws-kms-key-envelope-provider.server";
import { createTokenVaultFromEnvironment } from "../lib/token-vault-provider.server";
import {
  AWS_KMS_PROVIDER_ID,
  TokenVaultAuthenticationError,
  TokenVaultConfigurationError
} from "../lib/token-vault";

const kms = mockClient(KMSClient);

// Fake KMS that enforces EncryptionContext equality exactly like the real service:
// Decrypt only returns the plaintext when the EncryptionContext matches the one used at Encrypt.
function canonicalContext(context: Record<string, string> | undefined): string {
  const entries = Object.entries(context ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify(entries);
}

function installFakeKms() {
  kms.reset();
  kms.on(EncryptCommand).callsFake((input) => {
    const plaintext = Buffer.from(input.Plaintext as Uint8Array);
    const blob = Buffer.from(
      JSON.stringify({ pt: plaintext.toString("base64"), ec: canonicalContext(input.EncryptionContext) }),
      "utf8"
    );
    return { CiphertextBlob: new Uint8Array(blob) };
  });
  kms.on(DecryptCommand).callsFake((input) => {
    const parsed = JSON.parse(Buffer.from(input.CiphertextBlob as Uint8Array).toString("utf8")) as {
      pt: string;
      ec: string;
    };
    if (parsed.ec !== canonicalContext(input.EncryptionContext)) {
      throw new Error("InvalidCiphertextException");
    }
    return { Plaintext: new Uint8Array(Buffer.from(parsed.pt, "base64")) };
  });
}

const config = {
  keyId: "arn:aws:kms:us-east-1:140023375763:key/11111111-2222-3333-4444-555555555555",
  keyName: "appsell-token-vault-test",
  region: "us-east-1",
  timeoutMs: 2_000
};

const context = Buffer.from("appsell:test:subject:1", "utf8");
const dataKey = Buffer.alloc(32, 7);

beforeEach(() => installFakeKms());
afterEach(() => kms.reset());

describe("AwsKmsKeyEnvelopeProvider", () => {
  it("wraps and unwraps a data key round-trip", async () => {
    const provider = new AwsKmsKeyEnvelopeProvider(config);
    const envelope = await provider.wrapKey(dataKey, context);
    expect(envelope.provider).toBe(AWS_KMS_PROVIDER_ID);
    expect(envelope.kid).toBe("appsell-token-vault-test");
    expect(envelope.alg).toBe("AWS_KMS");

    const recovered = await provider.unwrapKey(envelope, context);
    expect(recovered.equals(dataKey)).toBe(true);
  });

  it("fails to unwrap when the authenticated context differs (AAD binding)", async () => {
    const provider = new AwsKmsKeyEnvelopeProvider(config);
    const envelope = await provider.wrapKey(dataKey, context);
    const tampered = Buffer.from("appsell:test:subject:2", "utf8");
    await expect(provider.unwrapKey(envelope, tampered)).rejects.toBeInstanceOf(TokenVaultAuthenticationError);
  });

  it("rejects an envelope with a mismatched kid", async () => {
    const provider = new AwsKmsKeyEnvelopeProvider(config);
    const envelope = await provider.wrapKey(dataKey, context);
    const other = new AwsKmsKeyEnvelopeProvider({ ...config, keyName: "appsell-token-vault-other" });
    await expect(other.unwrapKey(envelope, context)).rejects.toThrow();
  });

  it("is allowed under NODE_ENV=production (unlike local/openbao)", () => {
    vi.stubEnv("NODE_ENV", "production");
    try {
      expect(() => new AwsKmsKeyEnvelopeProvider(config)).not.toThrow();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("rejects invalid configuration", () => {
    expect(() => new AwsKmsKeyEnvelopeProvider({ ...config, region: "not-a-region" })).toThrow(
      TokenVaultConfigurationError
    );
    expect(() => new AwsKmsKeyEnvelopeProvider({ ...config, keyName: "bad name" })).toThrow(
      TokenVaultConfigurationError
    );
    expect(() => new AwsKmsKeyEnvelopeProvider({ ...config, keyId: "" })).toThrow(TokenVaultConfigurationError);
  });

  it("wires an aws-kms vault through the factory (APPSELL_VAULT_PROVIDER=aws-kms)", () => {
    const vault = createTokenVaultFromEnvironment({
      APPSELL_VAULT_PROVIDER: "aws-kms",
      APPSELL_KMS_KEY_ID: config.keyId,
      APPSELL_KMS_KEY_NAME: config.keyName,
      APPSELL_KMS_REGION: config.region
    });
    expect(vault).toBeTruthy();
    expect(() => vault.assertKeySeparation([])).not.toThrow();
  });
});
