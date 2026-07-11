import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";

const CAPABILITY = /^[a-z][a-z0-9_.:-]{0,79}$/;
const PATH = /^\/[a-z0-9_-]+(?:\/[a-z0-9_-]+)*$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const IDENTITY_VALUE = /^[^\p{Cc}\p{Cs}]{1,255}$/u;
const KEY_ID = /^[a-z0-9][a-z0-9._-]{0,39}$/;
const SERVICE_NAME = /^[a-z0-9][a-z0-9._:-]{0,119}$/;
const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);

export class InternalEnvelopeConfigurationError extends Error {}
export class InternalEnvelopeInputError extends Error {}

export interface InternalEnvelopeIdentity {
  issuer: string;
  sub: string;
  user_id: string;
}

export interface InternalEnvelopePayload {
  version: 1;
  kid: string;
  issuer: string;
  audience: string;
  session_ref: string;
  session_epoch: number;
  identity: InternalEnvelopeIdentity;
  capability: string;
  seller_organization_id: string | null;
  method: string;
  path: string;
  query_sha256: string;
  body_sha256: string;
  request_id: string;
  iat: number;
  exp: number;
}

export interface InternalSigningKey {
  id: string;
  key: Buffer;
}

export interface InternalEnvelopeConfig {
  issuer: string;
  audience: string;
  signingKey: InternalSigningKey;
}

export interface SignInternalEnvelopeInput {
  sessionId: string;
  sessionEpoch: number;
  identity: Omit<InternalEnvelopeIdentity, "user_id">;
  capability: string;
  sellerOrganizationId?: string | null;
  method: string;
  path: string;
  query?: Uint8Array;
  body: Uint8Array;
  requestId?: string;
  nowSeconds?: number;
  ttlSeconds?: number;
}

export function parseInternalSigningConfig(
  environment: Record<string, string | undefined>,
  forbiddenKeys: readonly Buffer[]
): InternalEnvelopeConfig {
  if (!Array.isArray(forbiddenKeys)) {
    throw new InternalEnvelopeConfigurationError("Internal key separation list is required.");
  }
  const issuer = environment.APPSELL_INTERNAL_ISSUER ?? "";
  const audience = environment.APPSELL_INTERNAL_AUDIENCE ?? "";
  if (!SERVICE_NAME.test(issuer) || !SERVICE_NAME.test(audience)) {
    throw new InternalEnvelopeConfigurationError("Internal issuer and audience are invalid.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(environment.APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON ?? "");
  } catch {
    throw new InternalEnvelopeConfigurationError("Internal HMAC keyring must be valid JSON.");
  }
  if (!Array.isArray(parsed) || parsed.length === 0 || parsed.length > 4) {
    throw new InternalEnvelopeConfigurationError("Internal HMAC keyring must contain 1 to 4 keys.");
  }

  const keys = parsed.map((entry) => parseKeyEntry(entry, forbiddenKeys));
  if (new Set(keys.map(({ id }) => id)).size !== keys.length) {
    throw new InternalEnvelopeConfigurationError("Internal HMAC key ids must be unique.");
  }
  const active = parsed.flatMap((entry, index) => isRecord(entry) && entry.active === true ? [keys[index]!] : []);
  if (active.length !== 1) {
    throw new InternalEnvelopeConfigurationError("Internal HMAC keyring must have exactly one active key.");
  }
  return { issuer, audience, signingKey: active[0]! };
}

export function signInternalEnvelope(
  input: SignInternalEnvelopeInput,
  config: InternalEnvelopeConfig
): { token: string; payload: InternalEnvelopePayload } {
  const now = input.nowSeconds ?? Math.floor(Date.now() / 1000);
  const ttl = input.ttlSeconds ?? 30;
  const requestId = input.requestId ?? randomUUID();
  const method = input.method.toUpperCase();
  const payload: InternalEnvelopePayload = {
    version: 1,
    kid: config.signingKey.id,
    issuer: config.issuer,
    audience: config.audience,
    session_ref: sessionReference(input.sessionId),
    session_epoch: input.sessionEpoch,
    identity: {
      issuer: input.identity.issuer,
      sub: input.identity.sub,
      user_id: deriveIdentityUserId(input.identity.issuer, input.identity.sub)
    },
    capability: input.capability,
    seller_organization_id: input.sellerOrganizationId ?? null,
    method,
    path: input.path,
    query_sha256: createHash("sha256").update(input.query ?? new Uint8Array()).digest("hex"),
    body_sha256: createHash("sha256").update(input.body).digest("hex"),
    request_id: requestId,
    iat: now,
    exp: now + ttl
  };
  validatePayload(payload, ttl);
  if (config.signingKey.key.length !== 32) {
    throw new InternalEnvelopeConfigurationError("Internal HMAC key must be 32 bytes.");
  }
  const serializedPayload = Buffer.from(JSON.stringify(payload), "utf8");
  if (serializedPayload.length > 4_096) {
    throw new InternalEnvelopeInputError("Internal envelope payload is too large.");
  }
  const encodedPayload = serializedPayload.toString("base64url");
  const signature = createHmac("sha256", config.signingKey.key)
    .update(`appsell.internal.v1.${encodedPayload}`, "ascii")
    .digest("base64url");
  return { token: `v1.${encodedPayload}.${signature}`, payload };
}

export function sessionReference(sessionId: string) {
  if (!sessionId || sessionId.length > 256) {
    throw new InternalEnvelopeInputError("Invalid session id.");
  }
  return createHash("sha256")
    .update("appsell.session-ref.v1\n", "utf8")
    .update(sessionId, "utf8")
    .digest("hex");
}

export function deriveIdentityUserId(issuer: string, sub: string) {
  if (!validIdentityValue(issuer) || !validIdentityValue(sub)) {
    throw new InternalEnvelopeInputError("Invalid identity binding.");
  }
  const namespace = Buffer.from("6ba7b8119dad11d180b400c04fd430c8", "hex");
  const bytes = createHash("sha1") // UUIDv5 requires SHA-1; this is identity naming, not authentication.
    .update(namespace)
    .update(`${issuer}\0${sub}`, "utf8")
    .digest()
    .subarray(0, 16);
  bytes[6] = (bytes[6]! & 0x0f) | 0x50;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function parseKeyEntry(entry: unknown, forbiddenKeys: readonly Buffer[]): InternalSigningKey {
  if (!isRecord(entry) || !hasExactKeys(entry, ["active", "id", "key"]) ||
      typeof entry.id !== "string" || !KEY_ID.test(entry.id) ||
      typeof entry.key !== "string" || typeof entry.active !== "boolean") {
    throw new InternalEnvelopeConfigurationError("Invalid internal HMAC key entry.");
  }
  const key = Buffer.from(entry.key, "base64");
  if (key.length !== 32 || key.toString("base64") !== entry.key) {
    throw new InternalEnvelopeConfigurationError("Internal HMAC keys must be canonical base64 for 32 bytes.");
  }
  if (forbiddenKeys.some((forbidden) => forbidden.length === 32 && timingSafeEqual(key, forbidden))) {
    throw new InternalEnvelopeConfigurationError("Internal HMAC key must be independent.");
  }
  return { id: entry.id, key };
}

function validatePayload(payload: InternalEnvelopePayload, ttl: number) {
  if (!KEY_ID.test(payload.kid) || !SERVICE_NAME.test(payload.issuer) || !SERVICE_NAME.test(payload.audience) ||
      !/^[0-9a-f]{64}$/.test(payload.session_ref) || !Number.isSafeInteger(payload.session_epoch) || payload.session_epoch < 1 ||
      !validIdentityValue(payload.identity.issuer) || !validIdentityValue(payload.identity.sub) ||
      payload.identity.user_id !== deriveIdentityUserId(payload.identity.issuer, payload.identity.sub) ||
      !CAPABILITY.test(payload.capability) || (payload.seller_organization_id !== null && !UUID.test(payload.seller_organization_id)) ||
      !ALLOWED_METHODS.has(payload.method) || payload.path.length > 512 || !PATH.test(payload.path) ||
      !/^[0-9a-f]{64}$/.test(payload.query_sha256) || !/^[0-9a-f]{64}$/.test(payload.body_sha256) ||
      !UUID.test(payload.request_id) || !Number.isSafeInteger(payload.iat) || payload.iat < 1 ||
      !Number.isSafeInteger(payload.exp) || ttl < 1 || ttl > 60 || payload.exp - payload.iat !== ttl) {
    throw new InternalEnvelopeInputError("Invalid internal envelope payload.");
  }
}

function validIdentityValue(value: string) {
  const length = Buffer.byteLength(value, "utf8");
  return !value.startsWith(" ") && !value.endsWith(" ") &&
    IDENTITY_VALUE.test(value) && length >= 3 && length <= 255;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]) {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}
