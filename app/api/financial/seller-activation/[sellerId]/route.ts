import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  ACTIVATE_SELLER_FINANCIAL_CSRF_ACTION,
  isCanonicalUuid
} from "@/lib/financial-activation";
import {
  activateSellerFinancial,
  DomainApiError,
  readSellerFinancialActivation
} from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import { csrfTokenForSession, verifyMutation } from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

// "Ativar vendas" (#382). O POST não leva nenhum dado de negócio do navegador,
// só o token de CSRF: o par do Financial vem da env da API. Quem pode ativar
// (flag por loja), o papel (só seller_owner) e o vínculo que já existe são
// decididos na API e no banco. Esta rota fica em app/api/financial/ e, por ser
// mais específica, vence o proxy [...path] para o Financial.

interface RouteContext {
  params: Promise<{ sellerId: string }>;
}

export async function GET(request: NextRequest, context: RouteContext) {
  const { sellerId } = await context.params;
  if (!isCanonicalUuid(sellerId)) return failure(404, "not_found");
  try {
    const sessionId = request.cookies.get(sessionCookieName())?.value;
    if (!sessionId) return failure(401, "unauthorized");
    const value = await oauthService().getSession(sessionId);
    if (!value) return failure(401, "unauthorized");
    const activation = await readSellerFinancialActivation(sessionId, value, sellerId);
    return NextResponse.json(
      {
        activation,
        csrf: activation.self_service
          ? { activate: csrfTokenForSession(sessionId, ACTIVATE_SELLER_FINANCIAL_CSRF_ACTION) }
          : null
      },
      { headers: noStore() }
    );
  } catch (error) {
    return activationError(error);
  }
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { sellerId } = await context.params;
  if (!isCanonicalUuid(sellerId)) return failure(404, "not_found");
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return failure(401, "unauthorized");
  try {
    if (!hasConfiguredOrigin(request, configuredPublicOrigin())) {
      return failure(403, "invalid_csrf");
    }
    const value = await oauthService().getSession(sessionId);
    if (!value) return failure(401, "unauthorized");
    let form: FormData | null;
    try {
      form = await readLimitedForm(request);
    } catch (error) {
      return error instanceof PayloadTooLargeError
        ? failure(413, "payload_too_large")
        : failure(422, "invalid_activation_request");
    }
    if (!form) return failure(422, "invalid_activation_request");
    const keys = [...form.keys()];
    if (keys.length !== 1 || keys[0] !== "_csrf") {
      return failure(422, "invalid_activation_request");
    }
    const csrf = form.get("_csrf");
    if (
      !verifyMutation(
        request,
        sessionId,
        ACTIVATE_SELLER_FINANCIAL_CSRF_ACTION,
        typeof csrf === "string" ? csrf : null
      )
    ) {
      return failure(403, "invalid_csrf");
    }
    const result = await activateSellerFinancial(sessionId, value, sellerId);
    return NextResponse.json(result, { headers: noStore() });
  } catch (error) {
    return activationError(error);
  }
}

function noStore() {
  return { "cache-control": "private, no-store" };
}

function failure(status: number, error: string) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}

function activationError(error: unknown) {
  if (error instanceof DomainApiError) return failure(error.status, error.code);
  return failure(
    503,
    isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" : "financial_activation_unavailable"
  );
}
