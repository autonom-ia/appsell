import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { NextRequest, NextResponse } from "next/server";

import manifest from "@/lib/demo-codex-assets.json";
import { authorizeDomainLearningAsset } from "@/lib/internal-api-client.server";
import {
  authenticatedLibrarySession,
  libraryError,
  libraryUnauthorized
} from "../../_route-shared";

interface AssetAuthorization {
  asset: {
    id: string;
    seed_key: string;
    file_name: string;
    title: string;
    media_type: string;
    byte_size: number;
    sha256: string;
  };
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ assetSlug: string }> }
) {
  try {
    const authenticated = await authenticatedLibrarySession(request);
    if (!authenticated) return libraryUnauthorized();
    const { assetSlug } = await context.params;
    const expected = manifest.find((asset) => asset.slug === assetSlug);
    if (!expected) return new NextResponse(null, { status: 404 });
    const authorized = await authorizeDomainLearningAsset(
      authenticated.sessionId,
      authenticated.session,
      expected.seller_id,
      expected.asset_id
    ) as AssetAuthorization;
    const metadata = authorized.asset;
    const expectedName = `${expected.slug}.pdf`;
    if (
      metadata.id !== expected.asset_id ||
      metadata.seed_key !== expected.slug ||
      metadata.file_name !== expectedName ||
      metadata.media_type !== "application/pdf" ||
      metadata.byte_size !== expected.bytes ||
      metadata.sha256 !== expected.sha256
    ) {
      return NextResponse.json({ error: "asset_manifest_mismatch" }, {
        status: 503,
        headers: { "cache-control": "private, no-store" }
      });
    }
    const payload = await readFile(join(process.cwd(), "output", "pdf", "codex", expectedName));
    if (
      payload.byteLength !== expected.bytes ||
      createHash("sha256").update(payload).digest("hex") !== expected.sha256
    ) {
      return NextResponse.json({ error: "asset_integrity_failure" }, {
        status: 503,
        headers: { "cache-control": "private, no-store" }
      });
    }
    return new NextResponse(payload, {
      headers: {
        "cache-control": "private, no-store",
        "content-disposition": `inline; filename="${expectedName}"`,
        "content-length": String(payload.byteLength),
        "content-type": "application/pdf",
        "x-content-type-options": "nosniff"
      }
    });
  } catch (error) {
    return libraryError(error);
  }
}
