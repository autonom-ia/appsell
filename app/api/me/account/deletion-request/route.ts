import { NextRequest, NextResponse } from "next/server";

import { parseAccountDeletionPayload } from "@/lib/account-status";
import { requestDomainAccountDeletion } from "@/lib/internal-api-client.server";
import { REQUEST_ACCOUNT_DELETION_CSRF_ACTION } from "@/lib/mutation-protection.server";
import {
  accountError,
  authorizeAccountMutation,
  mappedAccountError,
  noStore
} from "../_route-shared";

const DELETION_RATE_LIMIT = {
  name: "account-deletion-request",
  maxPerClient: 3,
  maxGlobal: 100,
  windowMs: 3_600_000
} as const;

// Registra o pedido; não apaga nada (Decisão 019). Enquanto houver pedido
// aberto — sem `account.deletion.completed` posterior — o banco devolve o
// mesmo registro: 200 em vez de 201.
export async function POST(request: NextRequest) {
  try {
    const gate = await authorizeAccountMutation(
      request,
      REQUEST_ACCOUNT_DELETION_CSRF_ACTION,
      DELETION_RATE_LIMIT
    );
    if (!gate.ok) return gate.response;
    const receipt = parseAccountDeletionPayload(
      await requestDomainAccountDeletion(gate.sessionId, gate.session)
    );
    if (!receipt) return accountError("invalid_account_response", 503);
    return NextResponse.json(
      { requested_at: receipt.requestedAt, already_requested: receipt.alreadyRequested },
      { status: receipt.alreadyRequested ? 200 : 201, headers: noStore() }
    );
  } catch (error) {
    return mappedAccountError(error);
  }
}
