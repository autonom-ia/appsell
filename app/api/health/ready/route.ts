import { NextResponse } from "next/server";

import { readinessState } from "@/lib/readiness.server";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = await readinessState();
  return NextResponse.json(
    {
      status: state.ready ? "ok" : "unavailable",
      redis: state.redis,
      api: state.api,
      storage_upload: state.storageUpload
    },
    {
      status: state.ready ? 200 : 503,
      headers: {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff"
      }
    }
  );
}
