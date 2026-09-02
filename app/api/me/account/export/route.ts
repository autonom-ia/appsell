import { NextRequest, NextResponse } from "next/server";

import { accountExportFilename, isAccountExportPayload } from "@/lib/account-status";
import { exportDomainAccountData } from "@/lib/internal-api-client.server";
import { EXPORT_ACCOUNT_DATA_CSRF_ACTION } from "@/lib/mutation-protection.server";
import {
  accountError,
  authorizeAccountMutation,
  mappedAccountError,
  noStore
} from "../_route-shared";

// Três por dez minutos por sessão. Quem exporta os próprios dados faz isso
// uma vez; o teto existe para a rota não virar bomba de auditoria nem de
// carga — o banco monta o documento inteiro a cada chamada.
const EXPORT_RATE_LIMIT = {
  name: "account-export",
  maxPerClient: 3,
  maxGlobal: 200,
  windowMs: 600_000
} as const;

export async function POST(request: NextRequest) {
  try {
    const gate = await authorizeAccountMutation(
      request,
      EXPORT_ACCOUNT_DATA_CSRF_ACTION,
      EXPORT_RATE_LIMIT
    );
    if (!gate.ok) return gate.response;
    const payload = await exportDomainAccountData(gate.sessionId, gate.session);
    if (!isAccountExportPayload(payload)) return accountError("invalid_account_response", 503);
    // É um arquivo, não uma página: o navegador salva em vez de renderizar, e
    // o nome carrega a data para a pessoa saber de quando é.
    return new NextResponse(JSON.stringify(payload, null, 2), {
      status: 200,
      headers: {
        ...noStore(),
        "content-type": "application/json; charset=utf-8",
        "content-disposition": `attachment; filename="${accountExportFilename(new Date())}"`
      }
    });
  } catch (error) {
    return mappedAccountError(error);
  }
}
