import { NextRequest, NextResponse } from "next/server";
import { oauthService } from "@/lib/oauth-service.server";
import { sessionCookieName } from "@/lib/cookies";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { demoProfileForSubject } from "@/lib/demo-config";
import { publicDemoRuntimeEnabled } from "@/lib/public-demo-runtime";
import { resolveSessionIdentity } from "@/lib/session-identity.server";
import {
  csrfCookieOptions,
  productCsrfCookieName,
  productPublishCsrfCookieName,
  productUpdateCsrfCookieName
} from "@/lib/cookies";
import {
  CREATE_PRODUCT_DRAFT_CSRF_ACTION,
  PUBLISH_PRODUCT_DRAFT_CSRF_ACTION,
  UPDATE_PRODUCT_DRAFT_CSRF_ACTION,
  csrfTokenForSession
} from "@/lib/mutation-protection.server";

export const dynamic = "force-dynamic";

// A resolução do papel falha FECHADA: se a API de domínio não responder, o
// payload sai sem identidade e o shell trata a sessão como não validada. Nunca
// assumimos "não é vendedor" — nem o contrário — a partir de uma consulta que
// não completou.
async function safeIdentity(
  sessionId: string,
  session: Awaited<ReturnType<ReturnType<typeof oauthService>["getSession"]>>
) {
  if (!session) return null;
  try {
    return await resolveSessionIdentity(sessionId, session);
  } catch {
    return null;
  }
}

export async function GET(request: NextRequest) {
  if (process.env.NODE_ENV === "production" && process.env.APPSELL_OAUTH_MODE !== "identity" &&
      !publicDemoRuntimeEnabled()) {
    return NextResponse.json({ authenticated: false }, {
      status: 401,
      headers: { "cache-control": "no-store" }
    });
  }
  try {
    const sessionId = request.cookies.get(sessionCookieName())?.value;
    const session = await oauthService().getSession(sessionId);
    if (!session || !sessionId) {
      return NextResponse.json({ authenticated: false }, {
        status: 401,
        headers: { "cache-control": "no-store" }
      });
    }
    const demoProfile = demoProfileForSubject(session.subject);
    // Só a sessão real precisa perguntar o papel à fonte de verdade. No modo
    // demo o papel já está na persona, e consultar a API de domínio ali
    // quebraria o runtime local que roda sem ela.
    const identity = demoProfile ? null : await safeIdentity(sessionId, session);
    const response = NextResponse.json({
      authenticated: true,
      expiresAt: session.expiresAt,
      runtime: process.env.APPSELL_OAUTH_MODE === "identity" ? "identity" : "local",
      demoProfile: demoProfile ? {
        persona: demoProfile.persona,
        email: demoProfile.email,
        label: demoProfile.label,
        home: demoProfile.home
      } : null,
      identity: identity ? {
        email: identity.email,
        emailVerified: identity.emailVerified,
        platformRoles: identity.platformRoles,
        sellers: identity.sellers.map((seller) => ({
          id: seller.id,
          slug: seller.slug,
          displayName: seller.displayName,
          role: seller.role
        }))
      } : null
    }, {
      status: 200,
      headers: { "cache-control": "no-store" }
    });
    // A sessão reconcilia as permissões de produto a cada carga do shell.
    // Sem isto, um vendedor que entrou enquanto a listagem estava fora ficava
    // sem elas pela sessão inteira: nada as reemitiria, e criar a loja de novo
    // não é caminho — o banco recusa um segundo onboarding para quem já é
    // owner ativo. A recíproca também vale: membership revogada perde as
    // permissões na carga seguinte, sem esperar o próximo login.
    if (identity) syncProductMutationCookies(response, sessionId, identity.sellers.length > 0);
    return response;
  } catch (error) {
    if (!isOAuthStoreUnavailable(error)) throw error;
    return NextResponse.json({ authenticated: false, error: "oauth_store_unavailable" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
}

function syncProductMutationCookies(
  response: NextResponse,
  sessionId: string,
  allowed: boolean
) {
  const grants = [
    [productCsrfCookieName(), CREATE_PRODUCT_DRAFT_CSRF_ACTION],
    [productUpdateCsrfCookieName(), UPDATE_PRODUCT_DRAFT_CSRF_ACTION],
    [productPublishCsrfCookieName(), PUBLISH_PRODUCT_DRAFT_CSRF_ACTION]
  ] as const;
  for (const [name, action] of grants) {
    if (allowed) {
      response.cookies.set(name, csrfTokenForSession(sessionId, action), csrfCookieOptions());
    } else {
      response.cookies.set(name, "", { ...csrfCookieOptions(), maxAge: 0 });
    }
  }
}
