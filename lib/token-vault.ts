import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
  timingSafeEqual
} from "node:crypto";
import { capacityLocalCiRuntimeEnabled } from "./capacity-local-ci-runtime";
import { publicDemoRuntimeEnabled } from "./public-demo-runtime";

const AES_KEY_BYTES = 32;
const GCM_IV_BYTES = 12;
const GCM_TAG_BYTES = 16;
const MAX_ENVELOPE_BYTES = 64 * 1024;
const MAX_CIPHERTEXT_BYTES = 32 * 1024;
const MAX_TOKEN_BYTES = 16 * 1024;
const MAX_SUBJECT_BYTES = 512;
const MAX_ISSUER_BYTES = 2 * 1024;
const MAX_EMAIL_BYTES = 320;
const MAX_SCOPE_BYTES = 256;
const MAX_SCOPES = 64;
const KEY_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const PROVIDER_ID_PATTERN = /^[a-z][a-z0-9._-]{0,63}$/;
const SESSION_HASH_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const NAMESPACE_PATTERN = /^appsell:[a-z0-9][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9_-]{5,63}$/;
const LOCAL_PROVIDER_ID = "local-aes-gcm-v1";
export const OPENBAO_TRANSIT_PROVIDER_ID = "openbao-transit-v1";
export const AWS_KMS_PROVIDER_ID = "aws-kms-v1";
const TOKEN_VAULT_VERSION = 2;
const OPENBAO_CIPHERTEXT_PATTERN = /^vault:v([1-9]\d*):([A-Za-z0-9+/]+={0,2})$/;
const MIN_OPENBAO_CIPHERTEXT_BYTES = 16;

export interface TokenBundle {
  subject: string;
  tokenType: "Bearer";
  accessToken: string;
  refreshToken: string;
  accessTokenExpiresAt: number;
  refreshTokenExpiresAt: number;
  scopes: string[];
  revision: number;
  identity?: {
    issuer: string;
    email: string;
    emailVerified: true;
  };
}

export interface TokenVaultContext {
  namespace: string;
  sessionHash: string;
  subject: string;
  revision: number;
}

interface AeadRecord {
  iv: string;
  ct: string;
  tag: string;
}

export interface LocalKeyEnvelope extends AeadRecord {
  provider: string;
  kid: string;
  alg: "A256GCM";
}

export interface OpenBaoTransitKeyEnvelope {
  provider: typeof OPENBAO_TRANSIT_PROVIDER_ID;
  kid: string;
  alg: "OPENBAO_TRANSIT";
  ciphertext: string;
  keyVersion: number;
}

export interface AwsKmsKeyEnvelope {
  provider: typeof AWS_KMS_PROVIDER_ID;
  kid: string;
  alg: "AWS_KMS";
  ciphertext: string;
}

export type KeyEnvelope = LocalKeyEnvelope | OpenBaoTransitKeyEnvelope | AwsKmsKeyEnvelope;

interface TokenVaultEnvelope {
  v: 2;
  alg: "A256GCM";
  keyEnvelope: KeyEnvelope;
  payload: AeadRecord;
}

export interface KeyEnvelopeProvider {
  wrapKey(dataKey: Buffer, authenticatedContext: Buffer): Promise<KeyEnvelope>;
  unwrapKey(envelope: KeyEnvelope, authenticatedContext: Buffer): Promise<Buffer>;
  assertKeySeparation(forbiddenKeys: Buffer[]): void;
}

export interface TokenVault {
  seal(bundle: TokenBundle, context: TokenVaultContext): Promise<string>;
  open(envelope: string, context: TokenVaultContext): Promise<TokenBundle>;
  assertKeySeparation(forbiddenKeys: Buffer[]): void;
}

export interface LocalKeyEntry {
  id: string;
  key: Buffer;
  active: boolean;
}

export class TokenVaultError extends Error {}
export class TokenVaultConfigurationError extends TokenVaultError {}
export class TokenVaultMalformedError extends TokenVaultError {}
export class TokenVaultAuthenticationError extends TokenVaultError {}
export class TokenVaultUnknownKeyError extends TokenVaultError {}

export class EnvelopeTokenVault implements TokenVault {
  constructor(private readonly keyProvider: KeyEnvelopeProvider) {}

  assertKeySeparation(forbiddenKeys: Buffer[]) {
    this.keyProvider.assertKeySeparation(forbiddenKeys);
  }

  async seal(bundle: TokenBundle, context: TokenVaultContext) {
    const normalizedBundle = validateTokenBundle(bundle, context.subject, context.revision);
    const normalizedContext = validateContext(context);
    const dataKey = randomBytes(AES_KEY_BYTES);
    const plaintext = Buffer.from(JSON.stringify(normalizedBundle), "utf8");
    try {
      if (plaintext.length > MAX_CIPHERTEXT_BYTES) {
        throw new TokenVaultMalformedError("Token vault bundle exceeds the encrypted payload limit.");
      }
      const payload = encryptAead(dataKey, plaintext, aad(normalizedContext, "payload"));
      const keyEnvelope = parseKeyEnvelope(
        await this.keyProvider.wrapKey(dataKey, aad(normalizedContext, "key-wrap"))
      );
      const envelope: TokenVaultEnvelope = {
        v: TOKEN_VAULT_VERSION,
        alg: "A256GCM",
        keyEnvelope,
        payload
      };
      const serialized = JSON.stringify(envelope);
      if (Buffer.byteLength(serialized) > MAX_ENVELOPE_BYTES) {
        throw new TokenVaultMalformedError("Token vault envelope exceeds the serialized limit.");
      }
      return serialized;
    } catch (error) {
      if (error instanceof TokenVaultError) throw error;
      throw new TokenVaultAuthenticationError("Token vault encryption failed.");
    } finally {
      dataKey.fill(0);
      plaintext.fill(0);
    }
  }

  async open(serialized: string, context: TokenVaultContext) {
    const normalizedContext = validateContext(context);
    const envelope = parseEnvelope(serialized);
    let dataKey: Buffer | undefined;
    let plaintext: Buffer | undefined;
    try {
      dataKey = await this.keyProvider.unwrapKey(envelope.keyEnvelope, aad(normalizedContext, "key-wrap"));
      if (dataKey.length !== AES_KEY_BYTES) {
        throw new TokenVaultAuthenticationError("Token vault data key is invalid.");
      }
      plaintext = decryptAead(dataKey, envelope.payload, aad(normalizedContext, "payload"));
      const parsed = JSON.parse(plaintext.toString("utf8")) as unknown;
      return validateTokenBundle(parsed, normalizedContext.subject, normalizedContext.revision);
    } catch (error) {
      if (error instanceof TokenVaultError) throw error;
      if (error instanceof SyntaxError) throw new TokenVaultMalformedError("Token vault payload is invalid.");
      throw new TokenVaultAuthenticationError("Token vault authentication failed.");
    } finally {
      dataKey?.fill(0);
      plaintext?.fill(0);
    }
  }
}

export class LocalAesKeyEnvelopeProvider implements KeyEnvelopeProvider {
  private readonly keys = new Map<string, Buffer>();
  private readonly activeKeyId: string;

  constructor(
    entries: LocalKeyEntry[],
    options: { nodeEnvironment?: string; forbiddenKeys?: Buffer[] } = {}
  ) {
    if (
      (process.env.NODE_ENV === "production" || options.nodeEnvironment === "production") &&
      !capacityLocalCiRuntimeEnabled() && !publicDemoRuntimeEnabled()
    ) {
      throw new TokenVaultConfigurationError("Local vault key provider is forbidden in production.");
    }
    if (!Array.isArray(entries) || entries.length === 0 || entries.length > 16) {
      throw new TokenVaultConfigurationError("Local vault keyring must contain between 1 and 16 keys.");
    }
    const active = entries.filter((entry) => entry.active);
    if (active.length !== 1) {
      throw new TokenVaultConfigurationError("Local vault keyring must contain exactly one active key.");
    }
    for (const entry of entries) {
      if (!KEY_ID_PATTERN.test(entry.id) || this.keys.has(entry.id)) {
        throw new TokenVaultConfigurationError("Local vault key IDs must be unique and use the approved format.");
      }
      if (!Buffer.isBuffer(entry.key) || entry.key.length !== AES_KEY_BYTES) {
        throw new TokenVaultConfigurationError("Local vault keys must contain exactly 32 bytes.");
      }
      for (const forbidden of options.forbiddenKeys ?? []) {
        if (Buffer.isBuffer(forbidden) && forbidden.length === entry.key.length && timingSafeEqual(forbidden, entry.key)) {
          throw new TokenVaultConfigurationError("Vault encryption keys must be distinct from other application keys.");
        }
      }
      for (const existing of this.keys.values()) {
        if (existing.length === entry.key.length && timingSafeEqual(existing, entry.key)) {
          throw new TokenVaultConfigurationError("Local vault key material must be unique across key IDs.");
        }
      }
      this.keys.set(entry.id, Buffer.from(entry.key));
    }
    this.activeKeyId = active[0]!.id;
  }

  static fromEnvironment(
    environment: Partial<Record<string, string | undefined>> = process.env,
    forbiddenKeys: Buffer[] = []
  ) {
    if (
      (process.env.NODE_ENV === "production" || environment.NODE_ENV === "production") &&
      !capacityLocalCiRuntimeEnabled(environment) && !publicDemoRuntimeEnabled(environment)
    ) {
      throw new TokenVaultConfigurationError("Local vault key provider is forbidden in production.");
    }
    const raw = environment.APPSELL_VAULT_LOCAL_KEYRING_B64_JSON ?? "";
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new TokenVaultConfigurationError("APPSELL_VAULT_LOCAL_KEYRING_B64_JSON must be valid JSON.");
    }
    if (!Array.isArray(parsed)) {
      throw new TokenVaultConfigurationError("APPSELL_VAULT_LOCAL_KEYRING_B64_JSON must be a key list.");
    }
    const entries = parsed.map((value) => parseEnvironmentKey(value));
    return new LocalAesKeyEnvelopeProvider(entries, {
      nodeEnvironment: environment.NODE_ENV,
      forbiddenKeys
    });
  }

  async wrapKey(dataKey: Buffer, authenticatedContext: Buffer) {
    if (!Buffer.isBuffer(dataKey) || dataKey.length !== AES_KEY_BYTES) {
      throw new TokenVaultAuthenticationError("Token vault data key is invalid.");
    }
    const key = this.keys.get(this.activeKeyId)!;
    const providerContext = keyEnvelopeAad(authenticatedContext, this.activeKeyId);
    return {
      provider: LOCAL_PROVIDER_ID,
      kid: this.activeKeyId,
      alg: "A256GCM" as const,
      ...encryptAead(key, dataKey, providerContext)
    };
  }

  async unwrapKey(envelope: KeyEnvelope, authenticatedContext: Buffer) {
    if (envelope.provider !== LOCAL_PROVIDER_ID || envelope.alg !== "A256GCM") {
      throw new TokenVaultMalformedError("Token vault key envelope provider is invalid.");
    }
    const key = this.keys.get(envelope.kid);
    if (!key) throw new TokenVaultUnknownKeyError("Token vault key is unavailable.");
    const providerContext = keyEnvelopeAad(authenticatedContext, envelope.kid);
    return decryptAead(key, {
      iv: envelope.iv,
      ct: envelope.ct,
      tag: envelope.tag
    }, providerContext, AES_KEY_BYTES);
  }

  assertKeySeparation(forbiddenKeys: Buffer[]) {
    for (const forbidden of forbiddenKeys) {
      if (!Buffer.isBuffer(forbidden)) continue;
      for (const key of this.keys.values()) {
        if (key.length === forbidden.length && timingSafeEqual(key, forbidden)) {
          throw new TokenVaultConfigurationError("Vault encryption keys must be distinct from other application keys.");
        }
      }
    }
  }
}

export function createLocalTokenVaultFromEnvironment(
  environment: Partial<Record<string, string | undefined>> = process.env,
  forbiddenKeys: Buffer[] = []
) {
  return new EnvelopeTokenVault(LocalAesKeyEnvelopeProvider.fromEnvironment(environment, forbiddenKeys));
}

function parseEnvironmentKey(value: unknown): LocalKeyEntry {
  const record = exactObject(
    value,
    ["id", "key", "active"],
    "Local vault key entry is invalid.",
    [],
    TokenVaultConfigurationError
  );
  if (typeof record.id !== "string" || typeof record.key !== "string" || typeof record.active !== "boolean") {
    throw new TokenVaultConfigurationError("Local vault key entry is invalid.");
  }
  const key = decodeCanonicalBase64(record.key, AES_KEY_BYTES, "Local vault key is invalid.");
  return { id: record.id, key, active: record.active };
}

function parseEnvelope(serialized: string): TokenVaultEnvelope {
  if (typeof serialized !== "string" || serialized.length === 0 || Buffer.byteLength(serialized) > MAX_ENVELOPE_BYTES) {
    throw new TokenVaultMalformedError("Token vault envelope is invalid.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    throw new TokenVaultMalformedError("Token vault envelope is invalid.");
  }
  const envelope = exactObject(parsed, ["v", "alg", "keyEnvelope", "payload"], "Token vault envelope is invalid.");
  if (envelope.v !== TOKEN_VAULT_VERSION || envelope.alg !== "A256GCM") {
    throw new TokenVaultMalformedError("Token vault envelope version or algorithm is invalid.");
  }
  const keyEnvelope = parseKeyEnvelope(envelope.keyEnvelope);
  const payload = parseAeadRecord(envelope.payload, undefined, "Token vault payload is invalid.");
  return { v: TOKEN_VAULT_VERSION, alg: "A256GCM", keyEnvelope, payload };
}

function parseKeyEnvelope(value: unknown): KeyEnvelope {
  if (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).alg === "OPENBAO_TRANSIT"
  ) {
    return parseOpenBaoTransitKeyEnvelope(value);
  }
  if (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).alg === "AWS_KMS"
  ) {
    return parseAwsKmsKeyEnvelope(value);
  }
  const record = exactObject(
    value,
    ["provider", "kid", "alg", "iv", "ct", "tag"],
    "Token vault key envelope is invalid."
  );
  if (
    typeof record.provider !== "string" ||
    !PROVIDER_ID_PATTERN.test(record.provider) ||
    typeof record.kid !== "string" ||
    !KEY_ID_PATTERN.test(record.kid) ||
    record.alg !== "A256GCM"
  ) {
    throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
  }
  const aead = parseAeadRecord(
    record,
    AES_KEY_BYTES,
    "Token vault key envelope is invalid.",
    ["provider", "kid", "alg"]
  );
  return {
    provider: record.provider,
    kid: record.kid,
    alg: "A256GCM",
    ...aead
  };
}

function parseOpenBaoTransitKeyEnvelope(value: unknown): OpenBaoTransitKeyEnvelope {
  const record = exactObject(
    value,
    ["provider", "kid", "alg", "ciphertext", "keyVersion"],
    "Token vault key envelope is invalid."
  );
  if (
    record.provider !== OPENBAO_TRANSIT_PROVIDER_ID ||
    typeof record.kid !== "string" ||
    !KEY_ID_PATTERN.test(record.kid) ||
    record.alg !== "OPENBAO_TRANSIT" ||
    typeof record.ciphertext !== "string" ||
    !positiveSafeInteger(record.keyVersion)
  ) {
    throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
  }
  const parsedVersion = parseOpenBaoTransitCiphertextVersion(record.ciphertext);
  if (parsedVersion !== record.keyVersion) {
    throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
  }
  return {
    provider: OPENBAO_TRANSIT_PROVIDER_ID,
    kid: record.kid,
    alg: "OPENBAO_TRANSIT",
    ciphertext: record.ciphertext,
    keyVersion: record.keyVersion
  };
}

function parseAwsKmsKeyEnvelope(value: unknown): AwsKmsKeyEnvelope {
  const record = exactObject(
    value,
    ["provider", "kid", "alg", "ciphertext"],
    "Token vault key envelope is invalid."
  );
  if (
    record.provider !== AWS_KMS_PROVIDER_ID ||
    typeof record.kid !== "string" ||
    !KEY_ID_PATTERN.test(record.kid) ||
    record.alg !== "AWS_KMS" ||
    typeof record.ciphertext !== "string" ||
    Buffer.byteLength(record.ciphertext, "utf8") > MAX_CIPHERTEXT_BYTES ||
    !isCanonicalBase64(record.ciphertext)
  ) {
    throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
  }
  return {
    provider: AWS_KMS_PROVIDER_ID,
    kid: record.kid,
    alg: "AWS_KMS",
    ciphertext: record.ciphertext
  };
}

function isCanonicalBase64(value: string) {
  if (value.length === 0) return false;
  const decoded = Buffer.from(value, "base64");
  return decoded.length > 0 && decoded.toString("base64") === value;
}

export function parseOpenBaoTransitCiphertextVersion(ciphertext: string) {
  if (typeof ciphertext !== "string" || Buffer.byteLength(ciphertext, "utf8") > MAX_CIPHERTEXT_BYTES) {
    throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
  }
  const match = OPENBAO_CIPHERTEXT_PATTERN.exec(ciphertext);
  const version = match ? Number(match[1]) : 0;
  const encoded = match?.[2] ?? "";
  const decoded = Buffer.from(encoded, "base64");
  if (
    !match ||
    !Number.isSafeInteger(version) ||
    version <= 0 ||
    decoded.length < MIN_OPENBAO_CIPHERTEXT_BYTES ||
    decoded.toString("base64") !== encoded
  ) {
    throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
  }
  return version;
}

function parseAeadRecord(
  value: unknown,
  expectedCiphertextBytes: number | undefined,
  message: string,
  allowedAdditionalKeys: string[] = []
): AeadRecord {
  const record = exactObject(value, ["iv", "ct", "tag"], message, allowedAdditionalKeys);
  if (typeof record.iv !== "string" || typeof record.ct !== "string" || typeof record.tag !== "string") {
    throw new TokenVaultMalformedError(message);
  }
  const iv = decodeCanonical(record.iv, GCM_IV_BYTES, message);
  const ciphertext = decodeCanonical(record.ct, expectedCiphertextBytes, message);
  const tag = decodeCanonical(record.tag, GCM_TAG_BYTES, message);
  if (ciphertext.length === 0 || ciphertext.length > MAX_CIPHERTEXT_BYTES) {
    throw new TokenVaultMalformedError(message);
  }
  return {
    iv: iv.toString("base64url"),
    ct: ciphertext.toString("base64url"),
    tag: tag.toString("base64url")
  };
}

function validateContext(context: TokenVaultContext): TokenVaultContext {
  if (
    !context ||
    !NAMESPACE_PATTERN.test(context.namespace) ||
    !SESSION_HASH_PATTERN.test(context.sessionHash) ||
    !validSubject(context.subject) ||
    !positiveSafeInteger(context.revision)
  ) {
    throw new TokenVaultMalformedError("Token vault context is invalid.");
  }
  return { ...context };
}

function validateTokenBundle(value: unknown, expectedSubject: string, expectedRevision: number): TokenBundle {
  const baseKeys = [
    "subject",
    "tokenType",
    "accessToken",
    "refreshToken",
    "accessTokenExpiresAt",
    "refreshTokenExpiresAt",
    "scopes",
    "revision"
  ];
  const hasIdentity = Boolean(
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.prototype.hasOwnProperty.call(value, "identity")
  );
  const record = exactObject(
    value,
    hasIdentity ? [...baseKeys, "identity"] : baseKeys,
    "Token vault bundle is invalid."
  );
  if (
    typeof record.subject !== "string" ||
    record.subject !== expectedSubject ||
    !validSubject(record.subject) ||
    record.tokenType !== "Bearer" ||
    !validToken(record.accessToken) ||
    !validToken(record.refreshToken) ||
    !positiveSafeInteger(record.accessTokenExpiresAt) ||
    !positiveSafeInteger(record.refreshTokenExpiresAt) ||
    record.refreshTokenExpiresAt <= record.accessTokenExpiresAt ||
    !positiveSafeInteger(record.revision) ||
    record.revision !== expectedRevision ||
    !Array.isArray(record.scopes) ||
    record.scopes.length > MAX_SCOPES
  ) {
    throw new TokenVaultMalformedError("Token vault bundle is invalid.");
  }
  const scopes = record.scopes;
  if (!scopes.every((scope) => typeof scope === "string" && scope.length > 0 && Buffer.byteLength(scope) <= MAX_SCOPE_BYTES)) {
    throw new TokenVaultMalformedError("Token vault bundle is invalid.");
  }
  const normalizedScopes = [...new Set(scopes)].sort();
  if (normalizedScopes.length !== scopes.length || normalizedScopes.some((scope, index) => scope !== scopes[index])) {
    throw new TokenVaultMalformedError("Token vault scopes must be unique and sorted.");
  }
  const identity = hasIdentity ? validateTokenIdentity(record.identity) : undefined;
  return {
    subject: record.subject,
    tokenType: "Bearer",
    accessToken: record.accessToken,
    refreshToken: record.refreshToken,
    accessTokenExpiresAt: record.accessTokenExpiresAt,
    refreshTokenExpiresAt: record.refreshTokenExpiresAt,
    scopes: normalizedScopes,
    revision: record.revision,
    ...(identity ? { identity } : {})
  };
}

function validateTokenIdentity(value: unknown): NonNullable<TokenBundle["identity"]> {
  const record = exactObject(
    value,
    ["issuer", "email", "emailVerified"],
    "Token vault identity is invalid."
  );
  if (
    typeof record.issuer !== "string" ||
    Buffer.byteLength(record.issuer, "utf8") > MAX_ISSUER_BYTES ||
    typeof record.email !== "string" ||
    Buffer.byteLength(record.email, "utf8") > MAX_EMAIL_BYTES ||
    record.email !== record.email.trim().toLowerCase() ||
    !/^[^\s@]+@[^\s@]+$/.test(record.email) ||
    record.emailVerified !== true
  ) {
    throw new TokenVaultMalformedError("Token vault identity is invalid.");
  }
  let issuer: URL;
  try {
    issuer = new URL(record.issuer);
  } catch {
    throw new TokenVaultMalformedError("Token vault identity is invalid.");
  }
  if (
    !["http:", "https:"].includes(issuer.protocol) ||
    issuer.username ||
    issuer.password ||
    issuer.search ||
    issuer.hash ||
    record.issuer !== issuer.toString().replace(/\/$/, "")
  ) {
    throw new TokenVaultMalformedError("Token vault identity is invalid.");
  }
  return {
    issuer: record.issuer,
    email: record.email,
    emailVerified: true
  };
}

function aad(context: TokenVaultContext, purpose: "payload" | "key-wrap") {
  return Buffer.from(JSON.stringify([
    "appsell-token-vault",
    TOKEN_VAULT_VERSION,
    purpose,
    context.namespace,
    context.sessionHash,
    context.subject,
    context.revision
  ]), "utf8");
}

function keyEnvelopeAad(authenticatedContext: Buffer, keyId: string) {
  return Buffer.from(JSON.stringify([
    "appsell-token-vault-key-provider",
    1,
    LOCAL_PROVIDER_ID,
    keyId,
    "A256GCM",
    authenticatedContext.toString("base64url")
  ]), "utf8");
}

function encryptAead(key: Buffer, plaintext: Buffer, authenticatedContext: Buffer): AeadRecord {
  const iv = randomBytes(GCM_IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, iv, { authTagLength: GCM_TAG_BYTES });
  cipher.setAAD(authenticatedContext, { plaintextLength: plaintext.length });
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return {
    iv: iv.toString("base64url"),
    ct: ciphertext.toString("base64url"),
    tag: cipher.getAuthTag().toString("base64url")
  };
}

function decryptAead(
  key: Buffer,
  record: AeadRecord,
  authenticatedContext: Buffer,
  expectedCiphertextBytes?: number
) {
  const parsed = parseAeadRecord(record, expectedCiphertextBytes, "Token vault AEAD record is invalid.");
  const iv = Buffer.from(parsed.iv, "base64url");
  const ciphertext = Buffer.from(parsed.ct, "base64url");
  const tag = Buffer.from(parsed.tag, "base64url");
  try {
    const decipher = createDecipheriv("aes-256-gcm", key, iv, { authTagLength: GCM_TAG_BYTES });
    decipher.setAAD(authenticatedContext, { plaintextLength: ciphertext.length });
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  } catch {
    throw new TokenVaultAuthenticationError("Token vault authentication failed.");
  }
}

function exactObject(
  value: unknown,
  requiredKeys: string[],
  message: string,
  allowedAdditionalKeys: string[] = [],
  ErrorType: new (message: string) => TokenVaultError = TokenVaultMalformedError
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new ErrorType(message);
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const allowed = [...requiredKeys, ...allowedAdditionalKeys].sort();
  if (keys.length !== allowed.length || keys.some((key, index) => key !== allowed[index])) {
    throw new ErrorType(message);
  }
  for (const key of requiredKeys) if (!(key in record)) throw new ErrorType(message);
  return record;
}

function decodeCanonical(
  value: string,
  expectedBytes: number | undefined,
  message: string,
  ErrorType: new (message: string) => TokenVaultError = TokenVaultMalformedError
) {
  let decoded: Buffer;
  try {
    decoded = Buffer.from(value, "base64url");
  } catch {
    throw new ErrorType(message);
  }
  if (
    decoded.toString("base64url") !== value ||
    (expectedBytes !== undefined && decoded.length !== expectedBytes)
  ) {
    throw new ErrorType(message);
  }
  return decoded;
}

function decodeCanonicalBase64(value: string, expectedBytes: number, message: string) {
  const decoded = Buffer.from(value, "base64");
  if (decoded.length !== expectedBytes || decoded.toString("base64") !== value) {
    throw new TokenVaultConfigurationError(message);
  }
  return decoded;
}

function validSubject(value: string) {
  return value.length > 0 && Buffer.byteLength(value) <= MAX_SUBJECT_BYTES && !/[\u0000-\u001f\u007f]/.test(value);
}

function validToken(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && Buffer.byteLength(value) <= MAX_TOKEN_BYTES;
}

function positiveSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}
