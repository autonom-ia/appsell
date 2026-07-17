import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(
    { status: "ok" },
    {
      status: 200,
      headers: {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff"
      }
    }
  );
}
