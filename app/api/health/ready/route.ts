import { NextResponse } from "next/server";

import { oauthService } from "@/lib/oauth-service.server";
import { storageUploadConfigurationReady } from "@/lib/storage-upload-policy.server";

export const dynamic = "force-dynamic";

export async function GET() {
  const [redis, api, storageUpload] = await Promise.all([
    redisReady(),
    domainApiReady(),
    storageUploadConfigurationReady()
  ]);
  const ready = redis && api && storageUpload;
  return NextResponse.json(
    {
      status: ready ? "ok" : "unavailable",
      redis,
      api,
      storage_upload: storageUpload
    },
    {
      status: ready ? 200 : 503,
      headers: {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff"
      }
    }
  );
}

async function redisReady() {
  try {
    return await oauthService().health();
  } catch {
    return false;
  }
}

async function domainApiReady() {
  try {
    const raw = process.env.APPSELL_DOMAIN_API_URL ?? "";
    const origin = new URL(raw);
    if (raw !== origin.origin || origin.username || origin.password) return false;
    const response = await fetch(new URL("/health/ready", origin), {
      cache: "no-store",
      signal: AbortSignal.timeout(2_000)
    });
    if (!response.ok) return false;
    const payload = await response.json() as unknown;
    return isReadyPayload(payload);
  } catch {
    return false;
  }
}

function isReadyPayload(value: unknown) {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    (value as Record<string, unknown>).status === "ok" &&
    (value as Record<string, unknown>).database === true;
}
