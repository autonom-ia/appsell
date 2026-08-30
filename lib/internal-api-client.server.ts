import "server-only";

import type { AssetMediaType } from "./asset-media";

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
    "catalog.access_link.create" | "catalog.access_link.read" |
    "catalog.access_link.update" |
    "library.read" | "library.enroll" | "library.asset.read" | "library.asset.download" |
    "learning.player.read" | "learning.progress.write" |
    "learning.pdf-study.read" | "learning.pdf-study.write" |
    "commerce.demo.checkout.read" | "commerce.demo.checkout.complete" |
    "commerce.payout.read" | "commerce.payout.manage" | "commerce.payout.sync" |
    "commerce.checkout.destination";
  sellerOrganizationId?: string;
  inspectionToken?: string;
  body?: Record<string, unknown>;
  capacityTrace?: string | null;
  learningAssetsContract?: "v2" | "v3";
  timeoutMs?: number;
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

// Provider-driven status sync: the caller is the Stripe Connect webhook route,
// which has NO user session. The envelope carries a fixed worker identity and
// NO seller — the API pins both (payout_sync_routes) and the database resolves
// the seller from the unique provider-account binding itself. The status flags
// come from a signature-verified provider event, never from a browser.
export async function syncPayoutAccountStatus(input: {
  provider: "stripe";
  providerAccountId: string;
  chargesEnabled: boolean;
  payoutsEnabled: boolean;
  detailsSubmitted: boolean;
  disabledReason: string | null;
}) {
  const config = internalServiceConfig();
  const path = "/v1/internal/payout-accounts/sync";
  const body = Buffer.from(
    JSON.stringify({
      provider: input.provider,
      provider_account_id: input.providerAccountId,
      charges_enabled: input.chargesEnabled,
      payouts_enabled: input.payoutsEnabled,
      details_submitted: input.detailsSubmitted,
      disabled_reason: input.disabledReason
    }),
    "utf8"
  );
  const { token } = signInternalEnvelope(
    {
      sessionId: "stripe-connect-webhook",
      sessionEpoch: 1,
      identity: {
        issuer: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
        sub: "stripe-connect-webhook-v1"
      },
      capability: "commerce.payout.sync",
      sellerOrganizationId: null,
      method: "POST",
      path,
      query: Buffer.alloc(0),
      body
    },
    config.signing
  );
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3_000);
  try {
    const response = await fetch(new URL(path, config.apiOrigin), {
      method: "POST",
      body,
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
    return payload as { updated: boolean; payout_account_id?: string };
  } catch (error) {
    if (error instanceof DomainApiError) throw error;
    throw new DomainApiError(503, "domain_api_unavailable");
  } finally {
    clearTimeout(timeout);
  }
}

// The checkout worker's destination lookup: which provider account receives
// this seller's money, and can it charge yet. Worker envelope (no user
// session, no seller claim, pinned subject) — the seller id is the lookup key
// from the storefront the buyer is viewing. An empty result means the seller
// has NO destination and the caller must refuse the direct charge; it must
// NEVER silently fall back to charging the platform's own account.
export async function resolveCheckoutPayoutDestination(sellerOrganizationId: string) {
  const config = internalServiceConfig();
  const path = `/v1/internal/sellers/${sellerOrganizationId}/payout-destination`;
  const { token } = signInternalEnvelope(
    {
      sessionId: "stripe-checkout",
      sessionEpoch: 1,
      identity: {
        issuer: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
        sub: "stripe-checkout-v1"
      },
      capability: "commerce.checkout.destination",
      sellerOrganizationId: null,
      method: "GET",
      path,
      query: Buffer.alloc(0),
      body: Buffer.alloc(0)
    },
    config.signing
  );
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3_000);
  try {
    const response = await fetch(new URL(path, config.apiOrigin), {
      method: "GET",
      headers: {
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
    return payload as {
      payout_destination: {
        provider: "stripe";
        provider_account_id: string;
        charges_enabled: boolean;
      } | null;
    };
  } catch (error) {
    if (error instanceof DomainApiError) throw error;
    throw new DomainApiError(503, "domain_api_unavailable");
  } finally {
    clearTimeout(timeout);
  }
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

export async function updateDomainProductCourseDraft(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  input: {
    idempotencyKey: string;
    expectedProductVersion: number;
    expectedCourseVersion: number;
    courseId: string;
    slug: string;
    productTitle: string;
    productDescription: string;
    courseTitle: string;
  }
) {
  return domainRequest(sessionId, session, {
    method: "PATCH",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/metadata`,
    capability: "catalog.draft.update",
    sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      course_id: input.courseId,
      expected_product_version: input.expectedProductVersion,
      expected_course_version: input.expectedCourseVersion,
      slug: input.slug,
      title: input.productTitle,
      description: input.productDescription,
      course_title: input.courseTitle
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
    accessMode?: "free" | "paid";
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
      expected_offer_version: input.expectedOfferVersion,
      // Publishing decides access. Omitting the mode keeps the pre-existing
      // behaviour, which is free access with the offer left in draft.
      access_mode: input.accessMode ?? "free"
    }
  });
}

export async function createDomainAccessLink(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  input: {
    idempotencyKey: string;
    label: string;
    tokenSha256: string;
    tokenPrefix: string;
    maxUses: number | null;
    expiresAt: string | null;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/access-links`,
    capability: "catalog.access_link.create",
    sellerOrganizationId,
    // Only the digest and the display prefix cross this boundary. The raw token
    // stays in the response to the seller's own browser and is never persisted.
    body: {
      idempotency_key: input.idempotencyKey,
      label: input.label,
      token_sha256: input.tokenSha256,
      token_prefix: input.tokenPrefix,
      max_uses: input.maxUses,
      expires_at: input.expiresAt
    }
  });
}

export async function listDomainAccessLinks(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/access-links`,
    capability: "catalog.access_link.read",
    sellerOrganizationId
  });
}

export async function listDomainAccessLinkRedemptions(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  accessLinkId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path:
      `/v1/me/sellers/${sellerOrganizationId}/products/${productId}` +
      `/access-links/${accessLinkId}/redemptions`,
    capability: "catalog.access_link.read",
    sellerOrganizationId
  });
}

export async function revokeDomainAccessLink(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  accessLinkId: string,
  input: { idempotencyKey: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path:
      `/v1/me/sellers/${sellerOrganizationId}/products/${productId}` +
      `/access-links/${accessLinkId}/revoke`,
    capability: "catalog.access_link.update",
    sellerOrganizationId,
    body: { idempotency_key: input.idempotencyKey }
  });
}

export async function rotateDomainAccessLink(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  accessLinkId: string,
  input: { idempotencyKey: string; tokenSha256: string; tokenPrefix: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path:
      `/v1/me/sellers/${sellerOrganizationId}/products/${productId}` +
      `/access-links/${accessLinkId}/rotate`,
    capability: "catalog.access_link.update",
    sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      token_sha256: input.tokenSha256,
      token_prefix: input.tokenPrefix
    }
  });
}

export async function redeemDomainAccessLink(
  sessionId: string,
  session: ServerSession,
  input: { idempotencyKey: string; tokenSha256: string }
) {
  // Buyer scoped on purpose: no sellerOrganizationId, which is what the domain
  // function requires before it will grant an entitlement.
  return domainRequest(sessionId, session, {
    method: "POST",
    path: "/v1/me/library/access-links/redeem",
    capability: "library.enroll",
    body: {
      idempotency_key: input.idempotencyKey,
      token_sha256: input.tokenSha256
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

export async function createDomainLessonMaterial(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  lessonId: string,
  input: CurriculumMutationInput & {
    kind: "external" | "embed";
    title: string;
    url: string | null;
    provider: "youtube" | "vimeo" | null;
    videoId: string | null;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/materials`,
    capability: "catalog.curriculum.create",
    sellerOrganizationId,
    body: curriculumBody(input, {
      kind: input.kind,
      title: input.title,
      url: input.url,
      provider: input.provider,
      video_id: input.videoId
    })
  });
}

export async function listDomainLessonMaterials(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  lessonId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/materials`,
    capability: "catalog.curriculum.read",
    sellerOrganizationId
  });
}

export async function archiveDomainLessonMaterial(
  sessionId: string,
  session: ServerSession,
  sellerOrganizationId: string,
  productId: string,
  courseId: string,
  moduleId: string,
  lessonId: string,
  materialId: string,
  input: CurriculumMutationInput
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/sellers/${sellerOrganizationId}/products/${productId}/courses/${courseId}/modules/${moduleId}/lessons/${lessonId}/materials/${materialId}/archive`,
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
    mediaType: AssetMediaType;
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

interface ProductCoverPath {
  sellerOrganizationId: string;
  productId: string;
}

function coverPath(input: ProductCoverPath) {
  return `/v1/me/sellers/${input.sellerOrganizationId}/products/${input.productId}/cover`;
}

export async function createDomainCoverUploadIntent(
  sessionId: string,
  session: ServerSession,
  input: ProductCoverPath & {
    idempotencyKey: string;
    mediaType: string;
    byteSize: number;
    sha256: string;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `${coverPath(input)}/upload-intents`,
    capability: "catalog.draft.update",
    sellerOrganizationId: input.sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      media_type: input.mediaType,
      byte_size: input.byteSize,
      sha256: input.sha256
    }
  });
}

export async function confirmDomainCoverUpload(
  sessionId: string,
  session: ServerSession,
  input: ProductCoverPath & {
    uploadId: string;
    idempotencyKey: string;
    expectedProductVersion: number;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `${coverPath(input)}/uploads/${input.uploadId}/confirm`,
    capability: "catalog.draft.update",
    sellerOrganizationId: input.sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      expected_product_version: input.expectedProductVersion
    }
  });
}

export async function clearDomainProductCover(
  sessionId: string,
  session: ServerSession,
  input: ProductCoverPath & { idempotencyKey: string; expectedProductVersion: number }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `${coverPath(input)}/clear`,
    capability: "catalog.draft.update",
    sellerOrganizationId: input.sellerOrganizationId,
    body: {
      idempotency_key: input.idempotencyKey,
      expected_product_version: input.expectedProductVersion
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

export async function grantDomainPublicProductAccess(
  sessionId: string,
  session: ServerSession,
  input: { sellerOrganizationId: string; productId: string; idempotencyKey: string }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: "/v1/me/library/enroll",
    capability: "library.enroll",
    body: {
      idempotency_key: input.idempotencyKey,
      seller_id: input.sellerOrganizationId,
      product_id: input.productId
    }
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
    capability: "library.asset.read",
    timeoutMs: 15_000
  });
}

/**
 * O endereço de um vídeo do PDF que o navegador consegue tocar.
 *
 * A origem costuma ser QuickTime, e o Chrome recusa esse container. Quem
 * prepara o MP4 equivalente é o domínio, que tem `ffmpeg`; o BFF só pede e
 * repassa. Enquanto o preparo não termina, a resposta vem com
 * `status: "preparing"` e o intervalo de nova tentativa.
 */
export async function readDomainPdfLinkVideo(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  assetId: string,
  linkId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}/pdf-links/${linkId}/video`,
    capability: "library.asset.read",
    timeoutMs: 20_000
  });
}

export async function readDomainLearningPdfStudy(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  assetId: string
) {
  return domainRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}/study`,
    capability: "learning.pdf-study.read"
  });
}

export async function mutateDomainLearningPdfStudy(
  sessionId: string,
  session: ServerSession,
  entitlementId: string,
  assetId: string,
  body: {
    idempotency_key: string;
    expected_revision: number;
    mutation: Record<string, unknown>;
  }
) {
  return domainRequest(sessionId, session, {
    method: "POST",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}/study`,
    capability: "learning.pdf-study.write",
    body
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
  page: number,
  licenseLabel: string
): Promise<DomainLearningPdfPage> {
  const encodedLicense = Buffer.from(licenseLabel, "utf8").toString("base64url");
  return domainBinaryRequest(sessionId, session, {
    method: "GET",
    path: `/v1/me/learning/items/${entitlementId}/assets/${assetId}/pages/${page}`,
    capability: "library.asset.read",
    query: `license=${encodeURIComponent(encodedLicense)}`
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
  const timeout = setTimeout(() => controller.abort(), request.timeoutMs ?? 3_000);
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
        "library.enroll",
        "library.asset.read",
        "library.asset.download",
        "learning.player.read",
        "learning.progress.write",
        "learning.pdf-study.read",
        "learning.pdf-study.write",
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
