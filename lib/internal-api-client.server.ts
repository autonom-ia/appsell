import "server-only";

import { timingSafeEqual } from "node:crypto";

import {
  parseInternalSigningConfig,
  signInternalEnvelope,
  type SignInternalEnvelopeInput
} from "./internal-envelope";

interface ServerSession {
  subject: string;
  createdAt: number;
  expiresAt: number;
  tokenRevision: number;
}

interface ApiRequest {
  method: "GET" | "POST" | "PUT";
  path: string;
  capability: "identity.sync" | "platform.grants.read" | "seller.list" | "seller.onboard" |
    "catalog.draft.read" | "catalog.draft.create";
  sellerOrganizationId?: string;
  body?: Record<string, unknown>;
}

export class DomainApiError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(code);
  }
}

export function isDomainApiConfigured() {
  return Boolean(process.env.APPSELL_DOMAIN_API_URL);
}

export async function syncDomainIdentity(sessionId: string, session: ServerSession) {
  return domainRequest(sessionId, session, {
    method: "PUT",
    path: "/v1/me",
    capability: "identity.sync",
    body: {
      email: localIdentityEmail(),
      email_verified: true
    }
  });
}

export async function readDomainIdentity(sessionId: string, session: ServerSession) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: "/v1/me",
    capability: "platform.grants.read"
  });
}

export async function listDomainSellers(sessionId: string, session: ServerSession) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: "/v1/me/sellers",
    capability: "seller.list"
  });
}

export async function onboardDomainSeller(
  sessionId: string,
  session: ServerSession,
  input: { idempotencyKey: string; slug: string; displayName: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: "/v1/me/sellers",
    capability: "seller.onboard",
    body: {
      idempotency_key: input.idempotencyKey,
      slug: input.slug,
      display_name: input.displayName
    }
  });
}

export async function listDomainProductDrafts(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/products`,
    capability: "catalog.draft.read",
    sellerOrganizationId
  });
}

export async function createDomainProductDraft(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  input: {
    idempotencyKey: string;
    slug: string;
    productTitle: string;
    productDescription: string;
    courseTitle: string;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products`,
    capability: "catalog.draft.create",
    sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      slug: input.slug,
      title: input.productTitle,
      description: input.productDescription,
      course_title: input.courseTitle
    }
  });
}

async function domainRequest(
  sessionId: string,
  session: ServerSession,
  request: ApiRequest
) {
  const config = internalConfig();
  const body = request.body ? Buffer.from(JSON.stringify(request.body), "utf8") : Buffer.alloc(0);
  const envelopeInput: SignInternalEnvelopeInput = {
    sessionId,
    sessionEpoch: session.tokenRevision,
    identity: {
      issuer: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
      sub: session.subject
    },
    capability: request.capability,
    sellerOrganizationId: request.sellerOrganizationId ?? null,
    method: request.method,
    path: request.path,
    body
  };
  const { token } = signInternalEnvelope(envelopeInput, config.signing);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3_000);
  try {
    const response = await fetch(new URL(request.path, config.apiOrigin), {
      method: request.method,
      body: request.method === "GET" ? undefined : body,
      headers: {
        "content-type": "application/json",
        "x-appsell-internal-envelope": token,
        "x-appsell-service-credential": config.serviceCredentialEncoded
      },
      cache: "no-store",
      signal: controller.signal
    });
    const payload = await response.json().catch(() => null) as unknown;
    if (!response.ok) {
      const code = isRecord(payload) && typeof payload.detail === "string"
        ? payload.detail
        : "domain_api_error";
      throw new DomainApiError(response.status, code);
    }
    return payload;
  } catch (error) {
    if (error instanceof DomainApiError) throw error;
    throw new DomainApiError(503, "domain_api_unavailable");
  } finally {
    clearTimeout(timeout);
  }
}

function internalConfig() {
  const forbidden = configuredNonInternalKeys();
  const signing = parseInternalSigningConfig(process.env, forbidden);
  const serviceCredentialEncoded = requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64");
  const serviceCredential = decodeCanonicalKey(serviceCredentialEncoded, "service credential");
  if ([signing.signingKey.key, ...forbidden].some((key) => timingSafeEqual(key, serviceCredential))) {
    throw new DomainApiError(503, "unsafe_internal_secret_reuse");
  }
  return {
    apiOrigin: configuredApiOrigin(),
    signing,
    serviceCredentialEncoded
  };
}

function configuredNonInternalKeys() {
  const keys: Buffer[] = [];
  for (const name of ["APPSELL_SESSION_HMAC_KEY_B64", "APPSELL_CSRF_HMAC_KEY_B64"]) {
    const value = process.env[name];
    if (value) keys.push(decodeCanonicalKey(value, name));
  }
  try {
    const keyring = JSON.parse(process.env.APPSELL_VAULT_LOCAL_KEYRING_B64_JSON ?? "[]");
    if (Array.isArray(keyring)) {
      for (const entry of keyring) {
        if (isRecord(entry) && typeof entry.key === "string") {
          keys.push(decodeCanonicalKey(entry.key, "vault key"));
        }
      }
    }
  } catch {
    throw new DomainApiError(503, "invalid_vault_keyring");
  }
  return keys;
}

function decodeCanonicalKey(value: string, label: string) {
  const decoded = Buffer.from(value, "base64");
  if (decoded.length !== 32 || decoded.toString("base64") !== value) {
    throw new DomainApiError(503, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return decoded;
}

function configuredApiOrigin() {
  const raw = requiredEnvironment("APPSELL_DOMAIN_API_URL");
  const parsed = new URL(raw);
  if (raw !== parsed.origin || parsed.username || parsed.password || parsed.pathname !== "/") {
    throw new DomainApiError(503, "invalid_domain_api_url");
  }
  if (process.env.NODE_ENV === "production" && parsed.protocol !== "https:") {
    throw new DomainApiError(503, "insecure_domain_api_url");
  }
  return parsed.origin;
}

function localIdentityEmail() {
  if (process.env.NODE_ENV === "production") {
    throw new DomainApiError(503, "real_identity_claims_required");
  }
  const email = requiredEnvironment("APPSELL_LOCAL_IDENTITY_EMAIL");
  if (email !== email.trim().toLowerCase() || !email.includes("@")) {
    throw new DomainApiError(503, "invalid_local_identity_email");
  }
  return email;
}

function requiredEnvironment(name: string) {
  const value = process.env[name];
  if (!value) throw new DomainApiError(503, `missing_${name.toLowerCase()}`);
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
