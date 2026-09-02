import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { DomainApiError } from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import { verifyMutation } from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

// As rotas da Minha conta (#351) compartilham a mesma porta: sessão do cookie,
// origem configurada, corpo com o token da ação e nada mais, e um teto de
// taxa por sessão — a exportação é superfície nova de PII e não pode virar
// um oráculo rápido.

type SessionOf = Awaited<ReturnType<ReturnType<typeof oauthService>["getSession"]>>;
export type AuthenticatedAccount = { sessionId: string; session: NonNullable<SessionOf> };

export interface AccountRateLimit {
  readonly name: string;
  readonly maxPerClient: number;
  readonly maxGlobal: number;
  readonly windowMs: number;
}

const ONLY_CSRF_FIELD = "_csrf";
const MAX_FORM_BYTES = 1_024;

export async function authenticatedAccountSession(
  request: NextRequest
): Promise<AuthenticatedAccount | null> {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  return session ? { sessionId, session } : null;
}

export async function authorizeAccountMutation(
  request: NextRequest,
  action: string,
  rateLimit: AccountRateLimit
): Promise<({ ok: true } & AuthenticatedAccount) | { ok: false; response: NextResponse }> {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return { ok: false, response: accountError("forbidden_mutation", 403) };
  }
  const service = oauthService();
  const session = await service.getSession(sessionId);
  if (!session) return { ok: false, response: accountError("unauthorized", 401) };

  let form: FormData | null;
  try {
    form = await readLimitedForm(request, MAX_FORM_BYTES);
  } catch (error) {
    return {
      ok: false,
      response: error instanceof PayloadTooLargeError
        ? accountError("payload_too_large", 413)
        : accountError("invalid_request_body", 422)
    };
  }
  // Só o token. Um campo a mais — um id de usuário, por exemplo — é pedido
  // malformado, não algo a ignorar.
  if (!form || !hasOnlyCsrf(form)) {
    return { ok: false, response: accountError("invalid_request_body", 422) };
  }
  const token = form.get(ONLY_CSRF_FIELD);
  if (!verifyMutation(request, sessionId, action, typeof token === "string" ? token : null)) {
    return { ok: false, response: accountError("forbidden_mutation", 403) };
  }
  // A chave é a PESSOA, não a sessão: abrir dez sessões não compra dez cotas.
  // O sujeito é o identificador estável que a sessão carrega.
  const allowed = await service.consumeRateLimit(rateLimit.name, session.subject, rateLimit);
  if (!allowed) return { ok: false, response: accountError("rate_limited", 429) };
  return { ok: true, sessionId, session };
}

function hasOnlyCsrf(form: FormData) {
  const keys = [...form.keys()];
  return keys.length === 1 && keys[0] === ONLY_CSRF_FIELD;
}

export function noStore() {
  return {
    "cache-control": "private, no-store, max-age=0",
    pragma: "no-cache",
    "x-content-type-options": "nosniff"
  };
}

export function accountError(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}

export function mappedAccountError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) return accountError("oauth_store_unavailable", 503);
  if (error instanceof DomainApiError) return accountError(error.code, error.status);
  return accountError("account_unavailable", 503);
}
