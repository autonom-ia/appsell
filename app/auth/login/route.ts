import { NextRequest } from "next/server";
import { beginAuthEntry } from "@/lib/auth-entry.server";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  return beginAuthEntry(request, "authorize");
}
