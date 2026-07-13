import { NextRequest, NextResponse } from "next/server";

import { readDomainLibrary } from "@/lib/internal-api-client.server";
import {
  authenticatedLibrarySession,
  libraryError,
  libraryUnauthorized
} from "./_route-shared";

export async function GET(request: NextRequest) {
  try {
    const authenticated = await authenticatedLibrarySession(request);
    if (!authenticated) return libraryUnauthorized();
    const payload = await readDomainLibrary(authenticated.sessionId, authenticated.session);
    return NextResponse.json(payload, {
      headers: { "cache-control": "private, no-store" }
    });
  } catch (error) {
    return libraryError(error);
  }
}
