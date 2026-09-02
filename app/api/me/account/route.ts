import { NextRequest, NextResponse } from "next/server";

import { parseAccountStatusPayload } from "@/lib/account-status";
import { readDomainAccountStatus } from "@/lib/internal-api-client.server";
import {
  accountError,
  authenticatedAccountSession,
  mappedAccountError,
  noStore
} from "./_route-shared";

// O que a tela precisa saber ao abrir: se já existe pedido de exclusão. O
// resto da identidade já vem de /api/session.
export async function GET(request: NextRequest) {
  try {
    const authenticated = await authenticatedAccountSession(request);
    if (!authenticated) return accountError("unauthorized", 401);
    const raw = await readDomainAccountStatus(authenticated.sessionId, authenticated.session);
    const status = parseAccountStatusPayload(raw);
    if (!status) return accountError("invalid_account_response", 503);
    return NextResponse.json(
      { deletion_requested_at: status.deletionRequestedAt },
      { headers: noStore() }
    );
  } catch (error) {
    return mappedAccountError(error);
  }
}
