import "server-only";

import { createHash, timingSafeEqual } from "node:crypto";
import demoCodexAssets from "./demo-codex-assets.json";
import { capacityLocalCiRuntimeEnabled } from "./capacity-local-ci-runtime";
import { trustedInternalApiHost } from "./domain-api-trusted-hosts";
import { publicDemoRuntimeEnabled } from "./public-demo-runtime";
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
  identity?: {
    issuer: string;
    email: string;
    emailVerified: true;
  } | null;
}

interface ApiRequest {
  method: "GET" | "POST" | "PUT" | "PATCH";
  path: string;
  query?: string;
  capability: "identity.sync" | "platform.grants.read" | "seller.list" | "seller.onboard" |
    "platform.demo.inspect.start" | "platform.demo.inspect.read" |
    "platform.demo.inspect.revoke" |
    "catalog.draft.read" | "catalog.draft.create" | "catalog.draft.update" |
    "catalog.offer.status.update" |
    "catalog.draft.preview" | "catalog.publication.read" | "catalog.publication.create" |
    "catalog.curriculum.read" | "catalog.curriculum.create" |
    "catalog.curriculum.update" | "catalog.curriculum.archive" |
    "catalog.curriculum.reorder" | "catalog.asset.read" | "catalog.asset.create" |
    "catalog.asset.upload.confirm" | "catalog.asset.upload.cancel" |
    "catalog.asset.archive" | "catalog.asset.policy.update" |
    "library.read" | "library.asset.read" | "library.asset.download" |
    "learning.player.read" | "learning.progress.write" |
    "commerce.demo.checkout.read" | "commerce.demo.checkout.complete" |
    "commerce.payout.read" | "commerce.payout.manage";
  sellerOrganizationId?: string;
  inspectionToken?: string;
  body?: Record<string, unknown>;
  capacityTrace?: string | null;
  learningAssetsContract?: "v2" | "v3";
}

export interface DemoInspectionProjection {
  inspection: { id: string; started_at: string; expires_at: string };
  seller: { id: string; slug: string; display_name: string; status: string };
  product: { id: string; slug: string; title: string; status: string };
  publication: {
    id: string;
    version: number;
    schema_version: number;
    published_at: string;
  };
  courses: Array<{ id: string; title: string; position: number }>;
}

export class DomainApiError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(code);
  }
}

export function isDomainApiConfigured() {
  return Boolean(process.env.APPSELL_DOMAIN_API_URL);
}

export async function readPublicStorefront(sellerSlug: string) {
  const config = internalServiceConfig();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3_000);
  try {
    const response = await fetch(
      new URL(`/v1/public/sellers/${encodeURIComponent(sellerSlug)}`, config.apiOrigin),
      {
        headers: { "x-appsell-service-credential": config.serviceCredentialEncoded },
        cache: "no-store",
        signal: controller.signal
      }
    );
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

export async function syncDomainIdentity(sessionId: string, session: ServerSession) {
  return domainRequest(sessionId, session, {
    method: "PUT",
    path: "/v1/me",
    capability: "identity.sync",
    body: {
      email: identityEmail(session),
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

export async function startDomainDemoInspection(
  sessionId: string,
  session: ServerSession,
  input: { reason: string; ttlSeconds: number }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: "/v1/platform/demo-inspections",
    capability: "platform.demo.inspect.start",
    body: { reason: input.reason, ttl_seconds: input.ttlSeconds }
  }) as Promise<{
    inspection_id: string;
    inspection_token: string;
    expires_at: string;
    seller: { id: string; slug: string; display_name: string };
  }>;
}

export async function readDomainDemoInspection(
  sessionId: string,
  session: ServerSession,
  inspectionId: string,
  inspectionToken: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/platform/demo-inspections/${inspectionId}`,
    capability: "platform.demo.inspect.read",
    inspectionToken
  }) as Promise<DemoInspectionProjection>;
}

export async function revokeDomainDemoInspection(
  sessionId: string,
  session: ServerSession,
  inspectionId: string,
  inspectionToken: string,
  reason: string
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/platform/demo-inspections/${inspectionId}/revoke`,
    capability: "platform.demo.inspect.revoke",
    inspectionToken,
    body: { reason }
  }) as Promise<{ inspection_id: string; status: "revoked"; revoked_at: string }>;
}

export interface SellerPayoutAccountProjection {
  payout_account_id: string;
  provider: string;
  provider_account_id: string;
  charges_enabled: boolean;
  payouts_enabled: boolean;
  details_submitted: boolean;
  disabled_reason: string | null;
  status_synced_at: string;
}

export async function readSellerPayoutAccount(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/payout-account`,
    capability: "commerce.payout.read",
    sellerOrganizationId
  }) as Promise<{ payout_account: SellerPayoutAccountProjection | null }>;
}

// The provider_account_id sent here must come ONLY from an authenticated
// accounts.create response (or a verified provider webhook) — never from
// anything the browser submitted. The domain API enforces membership and the
// database enforces capability + tenant + immutability, but the honesty of the
// account id itself starts at this call site.
export async function bindSellerPayoutAccount(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  input: {
    provider: "stripe";
    providerAccountId: string;
    chargesEnabled: boolean;
    payoutsEnabled: boolean;
    detailsSubmitted: boolean;
    disabledReason: string | null;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/payout-account`,
    capability: "commerce.payout.manage",
    sellerOrganizationId,
    body: {
      provider: input.provider,
      provider_account_id: input.providerAccountId,
      charges_enabled: input.chargesEnabled,
      payouts_enabled: input.payoutsEnabled,
      details_submitted: input.detailsSubmitted,
      disabled_reason: input.disabledReason
    }
  }) as Promise<{
    payout_account_id: string;
    provider: string;
    provider_account_id: string;
  }>;
}

export async function listDomainProductDrafts(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  cursor: string | null = null
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/products`,
    query: cursor ? `cursor=${encodeURIComponent(cursor)}` : undefined,
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
    offerUnitAmountMinor?: number;
    offerCurrency?: string;
    offerBillingPeriod?: "one_time" | "monthly" | "yearly";
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
      offer_summary: input.offerSummary,
      // Omitted price fields preserve the stored offer values (contract da 0018).
      ...(input.offerUnitAmountMinor === undefined
        ? {}
        : { offer_unit_amount_minor: input.offerUnitAmountMinor }),
      ...(input.offerCurrency === undefined ? {} : { offer_currency: input.offerCurrency }),
      ...(input.offerBillingPeriod === undefined
        ? {}
        : { offer_billing_period: input.offerBillingPeriod })
    }
  });
}

export async function transitionDomainOfferStatus(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  offerId: string,
  input: {
    idempotencyKey: string;
    expectedStatus: "draft" | "active" | "archived";
    status: "draft" | "active" | "archived";
    expectedOfferVersion: number;
  }
) {
  return domainRequest(sessionId, session, {
    method: "PATCH",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/offers/${offerId}/status`,
    capability: "catalog.offer.status.update",
    sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      expected_status: input.expectedStatus,
      status: input.status,
      expected_offer_version: input.expectedOfferVersion
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
    mediaType: "application/pdf" | "video/mp4";
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

export async function updateDomainLessonAssetPolicy(
  sessionId: string,
  session: ServerSession,
  input: LessonAssetPath & {
    assetId: string;
    idempotencyKey: string;
    expectedCourseVersion: number;
    contentRole: "course_content" | "support_material";
    downloadAllowed: boolean;
  }
) {
  return domainRequest(sessionId, session, {
    method: "PATCH",
    path: `${lessonAssetPath(input)}/${input.assetId}/policy`,
    capability: "catalog.asset.policy.update",
    sellerOrganizationId: input.sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      expected_course_version: input.expectedCourseVersion,
      content_role: input.contentRole,
      download_allowed: input.downloadAllowed
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

export async function readDomainDemoCheckout(sessionId: string, session: ServerSession) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: "/v1/me/demo-checkout",
    capability: "commerce.demo.checkout.read"
  });
}

export async function completeDomainDemoCheckout(
  sessionId: string,
  session: ServerSession,
  input: { idempotencyKey: string; couponCode: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: "/v1/me/demo-checkout",
    capability: "commerce.demo.checkout.complete",
    body: {
      idempotency_key: input.idempotencyKey,
      coupon_code: input.couponCode
    }
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

export async function authorizeDomainLearningAssetDownload(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  assetId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}/download`,
    capability: "library.asset.download"
  });
}

export async function readDomainLearningPdfManifest(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  assetId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}/pages`,
    capability: "library.asset.read"
  });
}

export interface DomainLearningPdfPage {
  bytes: Uint8Array;
  contentLength: number;
  pageCount: number;
  sha256: string;
}

export async function renderDomainLearningPdfPage(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  assetId: string,
  page: number
): Promise<DomainLearningPdfPage> {
  return domainBinaryRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}/pages/${page}`,
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
    capability: "learning.player.read",
    learningAssetsContract: "v3"
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
    capability: "learning.player.read",
    learningAssetsContract: "v3"
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
  requireDemoCapability(session.subject, request);
  const config = internalServiceConfig();
  const body = request.body ? Buffer.from(JSON.stringify(request.body), "utf8") : Buffer.alloc(0);
  const query = Buffer.from(request.query ?? "", "ascii");
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
    query,
    body
  };
  const { token } = signInternalEnvelope(envelopeInput, config.signing);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3_000);
  try {
    const requestUrl = new URL(request.path, config.apiOrigin);
    requestUrl.search = request.query ?? "";
    const capacityTrace = capacityProductCreateTelemetryEnabled() &&
      request.capability === "catalog.draft.create" &&
      validCapacityProductCreateTrace(request.capacityTrace)
      ? request.capacityTrace
      : null;
    const response = await fetch(requestUrl, {
      method: request.method,
      body: request.method === "GET" ? undefined : body,
      headers: {
          "content-type": "application/json",
          "x-appsell-internal-envelope": token,
          "x-appsell-service-credential": config.serviceCredentialEncoded,
          ...(request.inspectionToken
            ? { "x-appsell-inspection-token": request.inspectionToken }
            : {}),
          ...(capacityTrace ? { "x-appsell-capacity-trace": capacityTrace } : {}),
          ...(request.learningAssetsContract
            ? { "x-appsell-learning-assets-contract": request.learningAssetsContract }
            : {})
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

const MAX_DOMAIN_BINARY_BYTES = 8 * 1024 * 1024;

async function domainBinaryRequest(
  sessionId: string,
  session: ServerSession,
  request: ApiRequest
): Promise<DomainLearningPdfPage> {
  requireDemoCapability(session.subject, request);
  const config = internalServiceConfig();
  const query = Buffer.from(request.query ?? "", "ascii");
  const body = Buffer.alloc(0);
  const { token } = signInternalEnvelope({
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
    query,
    body
  }, config.signing);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20_000);
  try {
    const requestUrl = new URL(request.path, config.apiOrigin);
    requestUrl.search = request.query ?? "";
    const response = await fetch(requestUrl, {
      method: request.method,
      headers: {
        "x-appsell-internal-envelope": token,
        "x-appsell-service-credential": config.serviceCredentialEncoded
      },
      cache: "no-store",
      signal: controller.signal
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as unknown;
      const code = isRecord(payload) && typeof payload.detail === "string"
        ? payload.detail
        : "domain_api_error";
      throw new DomainApiError(response.status, code);
    }
    const contentLength = Number(response.headers.get("content-length"));
    const pageCount = Number(response.headers.get("x-appsell-page-count"));
    const sha256 = response.headers.get("x-appsell-sha256") ?? "";
    if (response.headers.get("content-type") !== "image/png" ||
      !Number.isSafeInteger(contentLength) || contentLength < 1 ||
      contentLength > MAX_DOMAIN_BINARY_BYTES ||
      !Number.isSafeInteger(pageCount) || pageCount < 1 || pageCount > 100 ||
      !/^[0-9a-f]{64}$/.test(sha256)) {
      throw new DomainApiError(503, "invalid_pdf_page_response");
    }
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength !== contentLength || buffer.byteLength > MAX_DOMAIN_BINARY_BYTES) {
      throw new DomainApiError(503, "invalid_pdf_page_response");
    }
    if (createHash("sha256").update(new Uint8Array(buffer)).digest("hex") !== sha256) {
      throw new DomainApiError(503, "invalid_pdf_page_response");
    }
    return { bytes: new Uint8Array(buffer), contentLength, pageCount, sha256 };
  } catch (error) {
    if (error instanceof DomainApiError) throw error;
    throw new DomainApiError(503, "domain_api_unavailable");
  } finally {
    clearTimeout(timeout);
  }
}

function requireDemoCapability(subject: string, request: ApiRequest) {
  const profile = demoProfileForSubject(subject);
  if (!profile) {
    if (publicDemoRuntimeEnabled()) throw new DomainApiError(403, "demo_subject_required");
    return;
  }
  if (request.capability === "identity.sync") return;
  if (profile.persona === "buyer" &&
      [
        "library.read",
        "library.asset.read",
        "library.asset.download",
        "learning.player.read",
        "learning.progress.write",
        "commerce.demo.checkout.read",
        "commerce.demo.checkout.complete"
      ].includes(request.capability)) {
    return;
  }
  if (profile.persona === "admin") {
    if (request.capability === "seller.list") return;
    if (["library.read", "library.asset.read"].includes(request.capability)) return;
    const demoSellerId = demoCodexAssets[0]?.seller_id;
    if (request.capability.startsWith("catalog.") && request.sellerOrganizationId === demoSellerId) {
      return;
    }
  }
  if (profile.persona === "superadmin" &&
      (request.capability === "platform.grants.read" ||
       request.capability.startsWith("platform.demo.inspect."))) {
    return;
  }
  throw new DomainApiError(403, `demo_${profile.persona}_capability_forbidden`);
}

export function internalServiceConfig() {
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
  if (
    process.env.NODE_ENV === "production" &&
    parsed.protocol !== "https:" &&
    !trustedInternalHttpApiOrigin(parsed) &&
    !capacityLocalCiRuntimeEnabled() &&
    !publicDemoRuntimeEnabled()
  ) {
    throw new DomainApiError(503, "insecure_domain_api_url");
  }
  return parsed.origin;
}

// Real production with the API as an in-cluster Docker service (private network,
// no TLS) is legitimate; the operator must name that host explicitly via
// APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS. Absent the flag, behavior is
// unchanged (fail closed).
function trustedInternalHttpApiOrigin(parsed: URL) {
  if (parsed.protocol !== "http:") return false;
  try {
    return trustedInternalApiHost(parsed.hostname);
  } catch {
    throw new DomainApiError(503, "invalid_domain_api_trusted_internal_hosts");
  }
}

function identityEmail(session: ServerSession) {
  if (session.identity?.emailVerified === true) return session.identity.email;
  const demo = demoProfileForSubject(session.subject);
  if (demo) return demo.email;
  if (process.env.NODE_ENV === "production" && !capacityLocalCiRuntimeEnabled() &&
      !publicDemoRuntimeEnabled()) {
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
