import { NextRequest, NextResponse } from "next/server";
import { sessionCookieName } from "./lib/cookies";
import { canAccessDemoPreview } from "./lib/demo-view";
import { oauthService } from "./lib/oauth-service.server";
import { demoProfileForSubject } from "./lib/demo-config";
import {
  assertPublicStorefrontRateLimit,
  PublicStorefrontRateLimitError
} from "./lib/public-storefront-rate-limit.server";

export async function proxy(request: NextRequest) {
  const publicStorefront = request.nextUrl.pathname.startsWith("/s/") ||
    request.nextUrl.pathname.startsWith("/api/public/storefronts/");
  if (publicStorefront) {
    try {
      await assertPublicStorefrontRateLimit(request.headers, request.nextUrl.pathname);
      return NextResponse.next();
    } catch (error) {
      const limited = error instanceof PublicStorefrontRateLimitError && error.code === "rate_limited";
      const status = limited ? 429 : 503;
      const headers = publicNoStoreHeaders();
      if (limited) headers.set("retry-after", "60");
      if (request.nextUrl.pathname.startsWith("/api/")) {
        return NextResponse.json({ error: limited ? "rate_limited" : "storefront_unavailable" }, { status, headers });
      }
      headers.set("content-type", "text/html; charset=utf-8");
      headers.set(
        "content-security-policy",
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
      );
      return new NextResponse(publicFailureHtml(limited), { status, headers });
    }
  }
  if (process.env.NODE_ENV === "production") {
    return NextResponse.redirect(new URL("/", request.url));
  }

  try {
    const session = await oauthService().getSession(request.cookies.get(sessionCookieName())?.value);
    if (!canAccessDemoPreview(session?.subject, process.env.NODE_ENV, process.env)) {
      return NextResponse.redirect(new URL("/", request.url));
    }
    const demoProfile = demoProfileForSubject(session?.subject);
    if (demoProfile?.persona === "buyer" && !request.nextUrl.pathname.startsWith("/library")) {
      return NextResponse.redirect(new URL("/library", request.url));
    }
    return NextResponse.next();
  } catch {
    return NextResponse.redirect(new URL("/?error=session_unavailable", request.url));
  }
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/products/:path*",
    "/library/:path*",
    "/s/:path*",
    "/api/public/storefronts/:path*"
  ],
};

function publicNoStoreHeaders() {
  return new Headers({
    "cache-control": "no-store, max-age=0",
    "cdn-cache-control": "no-store",
    "surrogate-control": "no-store"
  });
}

function publicFailureHtml(limited: boolean) {
  const eyebrow = limited ? "Muitas solicitações" : "Vitrine indisponível";
  const title = limited ? "A vitrine precisa de um instante." : "Não foi possível carregar esta vitrine.";
  const message = limited
    ? "Aguarde um minuto antes de tentar novamente. O limite protege o catálogo e os espaços dos produtores."
    : "O serviço de proteção do catálogo não respondeu. Nenhum conteúdo privado foi exibido.";
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>${eyebrow} | Autonom.ia Sell</title><style>*{box-sizing:border-box}body{margin:0;min-height:100vh;background:#f4f6fb;color:#0f172a;font:16px system-ui,sans-serif}header,footer{padding:20px max(16px,env(safe-area-inset-left));background:#fff;border-bottom:1px solid #dbe3ed}header a{min-height:44px;display:inline-flex;align-items:center;gap:10px;color:#1e3a8a;text-decoration:none;font-weight:800}header b{display:grid;width:36px;height:36px;place-items:center;border-radius:12px;background:#1e3a8a;color:#fff}main{width:min(760px,100%);min-height:calc(100vh - 150px);margin:auto;padding:64px max(16px,env(safe-area-inset-right)) 64px max(16px,env(safe-area-inset-left));display:flex;flex-direction:column;justify-content:center;align-items:flex-start}h1{margin:8px 0 16px;font-size:clamp(2.4rem,10vw,4.6rem);line-height:1.05}p{max-width:58ch;line-height:1.65;color:#475569}.eyebrow{color:#1e3a8a;font-size:.75rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}nav{display:flex;flex-wrap:wrap;gap:10px}nav a{min-height:48px;padding:0 18px;display:inline-flex;align-items:center;border:1px solid #cbd5e1;border-radius:14px;background:#fff;color:#1e3a8a;text-decoration:none;font-weight:800}.primary{background:#1e3a8a;color:#fff}a:focus-visible{outline:3px solid #2563b8;outline-offset:3px}footer{border-top:1px solid #dbe3ed;border-bottom:0;color:#475569}</style></head><body><header><a href="/" aria-label="Autonom.ia Sell — início"><b>a</b> sell.</a></header><main><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${message}</p><nav aria-label="Ações de recuperação"><a class="primary" href="">Tentar novamente</a><a href="/">Ir para o início</a></nav></main><footer>Autonom.ia Sell</footer></body></html>`;
}
