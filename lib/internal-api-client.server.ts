import "server-only";

import { timingSafeEqual } from "node:crypto";
import { demoProfileForSubject } from "./demo-config";
import {
  capacityProductCreateTelemetryEnabled,
  validCapacityProductCreateTrace
} from "./capacity-product-create-telemetry";

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
  method: "GET" | "POST" | "PUT" | "PATCH";
  path: string;
  capability: "identity.sync" | "platform.grants.read" | "seller.list" | "seller.onboard" |
    "catalog.draft.read" | "catalog.draft.create" | "catalog.draft.update" |
    "catalog.draft.preview" | "catalog.publication.read" | "catalog.publication.create" |
    "catalog.curriculum.read" | "catalog.curriculum.create" |
    "catalog.curriculum.update" | "catalog.curriculum.archive" |
    "catalog.curriculum.reorder" | "catalog.asset.read" | "catalog.asset.create" |
    "catalog.asset.upload.confirm" | "catalog.asset.upload.cancel" |
    "catalog.asset.archive" | "library.read" | "library.asset.read" |
    "learning.player.read" | "learning.progress.write";
  sellerOrganizationId?: string;
  body?: Record<string, unknown>;
  capacityTrace?: string | null;
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
      email: localIdentityEmail(session.subject),
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
    capacityTrace?: string | null;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products`,
    capability: "catalog.draft.create",
    sellerOrganizationId,
    capacityTrace: input.capacityTrace,
    body: {
      idempotency_key: input.idempotencyKey,
      slug: input.slug,
      title: input.productTitle,
      description: input.productDescription,
      course_title: input.courseTitle
    }
  });
}

export async function readDomainProductDraft(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}`,
    capability: "catalog.draft.read",
    sellerOrganizationId
  });
}

export async function updateDomainProductDraft(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  input: {
    idempotencyKey: string;
    expectedProductVersion: number;
    expectedCourseVersion: number;
    expectedOfferVersion: number;
    courseId: string;
    offerId: string;
    slug: string;
    productTitle: string;
    productDescription: string;
    courseTitle: string;
    offerTitle: string;
    offerSummary: string;
  }
) {
  return domainRequest(sessionId, session, {
    method: "PATCH",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}`,
    capability: "catalog.draft.update",
    sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      course_id: input.courseId,
      offer_id: input.offerId,
      expected_product_version: input.expectedProductVersion,
      expected_course_version: input.expectedCourseVersion,
      expected_offer_version: input.expectedOfferVersion,
      slug: input.slug,
      title: input.productTitle,
      description: input.productDescription,
      course_title: input.courseTitle,
      offer_title: input.offerTitle,
      offer_summary: input.offerSummary
    }
  });
}

export async function previewDomainProductDraft(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/preview`,
    capability: "catalog.draft.preview",
    sellerOrganizationId
  });
}

export async function publishDomainProductDraft(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  input: {
    idempotencyKey: string;
    expectedProductVersion: number;
    expectedOfferVersion: number;
    offerId: string;
    courseVersions: Array<{ id: string; version: number }>;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/publications`,
    capability: "catalog.publication.create",
    sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      offer_id: input.offerId,
      expected_product_version: input.expectedProductVersion,
      course_versions: input.courseVersions,
      expected_offer_version: input.expectedOfferVersion
    }
  });
}

export async function readDomainCurriculum(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/curriculum`,
    capability: "catalog.curriculum.read",
    sellerOrganizationId
  });
}

interface CurriculumMutationInput {
  idempotencyKey: string;
  expectedCourseVersion: number;
}

export async function createDomainCourseModule(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  input: CurriculumMutationInput & { title: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules`,
    capability: "catalog.curriculum.create",
    sellerOrganizationId,
    body: curriculumBody(input, { title: input.title })
  });
}

export async function updateDomainCourseModule(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  input: CurriculumMutationInput & { title: string }
) {
  return domainRequest(sessionId, session, {
    method: "PATCH",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}`,
    capability: "catalog.curriculum.update",
    sellerOrganizationId,
    body: curriculumBody(input, { title: input.title })
  });
}

export async function archiveDomainCourseModule(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  input: CurriculumMutationInput
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/archive`,
    capability: "catalog.curriculum.archive",
    sellerOrganizationId,
    body: curriculumBody(input)
  });
}

export async function reorderDomainCourseModules(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  input: CurriculumMutationInput & { orderedIds: string[] }
) {
  return domainRequest(sessionId, session, {
    method: "PUT",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/order`,
    capability: "catalog.curriculum.reorder",
    sellerOrganizationId,
    body: curriculumBody(input, { ordered_ids: input.orderedIds })
  });
}

export async function createDomainCourseLesson(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  input: CurriculumMutationInput & { title: string; content: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons`,
    capability: "catalog.curriculum.create",
    sellerOrganizationId,
    body: curriculumBody(input, { title: input.title, kind: "text", content: input.content })
  });
}

export async function updateDomainCourseLesson(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  lessonId: string,
  input: CurriculumMutationInput & { title: string; content: string }
) {
  return domainRequest(sessionId, session, {
    method: "PATCH",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}`,
    capability: "catalog.curriculum.update",
    sellerOrganizationId,
    body: curriculumBody(input, { title: input.title, kind: "text", content: input.content })
  });
}

export async function archiveDomainCourseLesson(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  lessonId: string,
  input: CurriculumMutationInput
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/archive`,
    capability: "catalog.curriculum.archive",
    sellerOrganizationId,
    body: curriculumBody(input)
  });
}

export async function reorderDomainCourseLessons(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  input: CurriculumMutationInput & { orderedIds: string[] }
) {
  return domainRequest(sessionId, session, {
    method: "PUT",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/order`,
    capability: "catalog.curriculum.reorder",
    sellerOrganizationId,
    body: curriculumBody(input, { ordered_ids: input.orderedIds })
  });
}

interface LessonAssetPath {
  sellerOrganizationId: string;
  productId: string;
  courseId: string;
  moduleId: string;
  lessonId: string;
}

function lessonAssetPath(input: LessonAssetPath) {
  return `/v1/me/sellers/${input.sellerOrganizationId}/products/${input.productId}`
    + `/courses/${input.courseId}/modules/${input.moduleId}/lessons/${input.lessonId}/assets`;
}

export async function readDomainLessonAssets(
  sessionId: string,
  session: ServerSession,
  input: LessonAssetPath
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: lessonAssetPath(input),
    capability: "catalog.asset.read",
    sellerOrganizationId: input.sellerOrganizationId
  });
}

export async function createDomainAssetUploadIntent(
  sessionId: string,
  session: ServerSession,
  input: LessonAssetPath & {
    idempotencyKey: string;
    expectedCourseVersion: number;
    existingAssetId?: string;
    title: string;
    fileName: string;
    mediaType: "application/pdf";
    byteSize: number;
    sha256: string;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `${lessonAssetPath(input)}/upload-intents`,
    capability: "catalog.asset.create",
    sellerOrganizationId: input.sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      expected_course_version: input.expectedCourseVersion,
      ...(input.existingAssetId ? { existing_asset_id: input.existingAssetId } : {}),
      title: input.title,
      file_name: input.fileName,
      media_type: input.mediaType,
      byte_size: input.byteSize,
      sha256: input.sha256
    }
  });
}

export async function readDomainAssetUploadStatus(
  sessionId: string,
  session: ServerSession,
  input: LessonAssetPath & { uploadId: string }
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `${lessonAssetPath(input)}/uploads/${input.uploadId}`,
    capability: "catalog.asset.read",
    sellerOrganizationId: input.sellerOrganizationId
  });
}

export async function confirmDomainAssetUpload(
  sessionId: string,
  session: ServerSession,
  input: LessonAssetPath & { uploadId: string; idempotencyKey: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `${lessonAssetPath(input)}/uploads/${input.uploadId}/confirm`,
    capability: "catalog.asset.upload.confirm",
    sellerOrganizationId: input.sellerOrganizationId,
    body: { idempotency_key: input.idempotencyKey }
  });
}

export async function cancelDomainAssetUpload(
  sessionId: string,
  session: ServerSession,
  input: LessonAssetPath & { uploadId: string; idempotencyKey: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `${lessonAssetPath(input)}/uploads/${input.uploadId}/cancel`,
    capability: "catalog.asset.upload.cancel",
    sellerOrganizationId: input.sellerOrganizationId,
    body: { idempotency_key: input.idempotencyKey }
  });
}

export async function archiveDomainLessonAsset(
  sessionId: string,
  session: ServerSession,
  input: LessonAssetPath & {
    assetId: string;
    idempotencyKey: string;
    expectedCourseVersion: number;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `${lessonAssetPath(input)}/${input.assetId}/archive`,
    capability: "catalog.asset.archive",
    sellerOrganizationId: input.sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      expected_course_version: input.expectedCourseVersion
    }
  });
}

export async function readDomainLibrary(sessionId: string, session: ServerSession) {
  return readDomainBuyerProducts(sessionId, session);
}

export async function readDomainBuyerProducts(sessionId: string, session: ServerSession) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: "/v1/me/products",
    capability: "library.read"
  });
}

export async function readDomainBuyerSellerProducts(
  sessionId: string,
  session: ServerSession,
  entitlementId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/products/${entitlementId}/seller-products`,
    capability: "library.read"
  });
}

export async function authorizeDomainLearningAsset(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  assetId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}`,
    capability: "library.asset.read"
  });
}

export async function readDomainLearningResume(
  sessionId: string,
  session: ServerSession,
  entitlementId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/resume`,
    capability: "learning.player.read"
  });
}

export async function readDomainLearningLesson(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  lessonId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/lessons/${lessonId}`,
    capability: "learning.player.read"
  });
}

interface LearningProgressInput {
  idempotencyKey: string;
  expectedVersion: number;
  progressPercent?: number;
}

export async function updateDomainLessonProgress(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  lessonId: string,
  input: LearningProgressInput & { progressPercent: number }
) {
  return domainRequest(sessionId, session, {
    method: "PUT",
    path: `/v1/me/learning/items/${entitlementId}/lessons/${lessonId}/progress`,
    capability: "learning.progress.write",
    body: learningProgressBody(input)
  });
}

export async function completeDomainLesson(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  lessonId: string,
  input: LearningProgressInput
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/learning/items/${entitlementId}/lessons/${lessonId}/complete`,
    capability: "learning.progress.write",
    body: learningProgressBody(input)
  });
}

function learningProgressBody(input: LearningProgressInput) {
  return {
    idempotency_key: input.idempotencyKey,
    expected_version: input.expectedVersion,
    ...(input.progressPercent === undefined
      ? {}
      : { progress_percent: input.progressPercent })
  };
}

function curriculumBody(
  input: CurriculumMutationInput,
  extra: Record<string, unknown> = {}
) {
  return {
    idempotency_key: input.idempotencyKey,
    expected_course_version: input.expectedCourseVersion,
    ...extra
  };
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
    const capacityTrace = capacityProductCreateTelemetryEnabled() &&
      request.capability === "catalog.draft.create" &&
      validCapacityProductCreateTrace(request.capacityTrace)
      ? request.capacityTrace
      : null;
    const response = await fetch(new URL(request.path, config.apiOrigin), {
      method: request.method,
      body: request.method === "GET" ? undefined : body,
      headers: {
        "content-type": "application/json",
        "x-appsell-internal-envelope": token,
        "x-appsell-service-credential": config.serviceCredentialEncoded,
        ...(capacityTrace ? { "x-appsell-capacity-trace": capacityTrace } : {})
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

function localIdentityEmail(subject: string) {
  const demo = demoProfileForSubject(subject);
  if (demo) return demo.email;
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
