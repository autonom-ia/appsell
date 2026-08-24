import { NextRequest, NextResponse } from "next/server";

import { renderDomainLearningPdfPage } from "@/lib/internal-api-client.server";
import { learningLicenseLabel } from "@/lib/learning-license.server";
import {
  authenticatedLearningSession,
  learningAssetPageIds,
  learningError,
  learningNotFound,
  learningUnauthorized,
  type LearningAssetPageRouteContext
} from "../../../../../_route-shared";

export async function GET(request: NextRequest, context: LearningAssetPageRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetPageIds(context);
    if (!ids) return learningNotFound();
    const page = await renderDomainLearningPdfPage(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.assetId,
      ids.page,
      learningLicenseLabel(authenticated.session, ids.entitlementId)
    );
    const body = new ArrayBuffer(page.bytes.byteLength);
    new Uint8Array(body).set(page.bytes);
    return new NextResponse(body, {
      status: 200,
      headers: {
        "cache-control": "private, no-store, max-age=0",
        "content-disposition": `inline; filename="page-${ids.page}.png"`,
        "content-length": String(page.contentLength),
        "content-security-policy": "sandbox",
        "content-type": "image/png",
        "cross-origin-resource-policy": "same-origin",
        pragma: "no-cache",
        "referrer-policy": "no-referrer",
        "surrogate-control": "no-store",
        vary: "Cookie",
        "x-appsell-page-count": String(page.pageCount),
        "x-appsell-sha256": page.sha256,
        "x-content-type-options": "nosniff"
      }
    });
  } catch (error) {
    return learningError(error);
  }
}
