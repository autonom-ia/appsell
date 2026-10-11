import { NextRequest, NextResponse } from "next/server";

import { isPublicSlug } from "@/lib/public-storefront";

export function GET(request: NextRequest) {
  const seller = request.nextUrl.searchParams.get("seller");
  const product = request.nextUrl.searchParams.get("product");
  const retryPath = isPublicSlug(seller) && (product === null || isPublicSlug(product))
    ? `/s/${seller}${product ? `/${product}` : ""}`
    : "/";
  const html = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>Vitrine indisponível | Autonom.ia Sell</title><style>${failureStyles()}</style></head><body><header><a href="/" aria-label="Autonom.ia Sell — início"><b>a</b> sell.</a></header><main><p class="eyebrow">Vitrine indisponível</p><h1>Não foi possível carregar esta vitrine.</h1><p>O catálogo público não respondeu. Aguarde um instante e tente novamente.</p><nav aria-label="Ações de recuperação"><a class="primary" href="${retryPath}">Tentar novamente</a><a href="/">Ir para o início</a></nav></main><footer>Autonom.ia Sell</footer></body></html>`;
  return new NextResponse(html, {
    status: 503,
    headers: {
      "cache-control": "no-store, max-age=0",
      "cdn-cache-control": "no-store",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
      "content-type": "text/html; charset=utf-8",
      "retry-after": "30",
      "surrogate-control": "no-store"
    }
  });
}

function failureStyles() {
  return "*{box-sizing:border-box}body{margin:0;min-height:100vh;background:#f4f6fb;color:#0f172a;font:16px system-ui,sans-serif}header,footer{padding:20px max(16px,env(safe-area-inset-left));background:#fff;border-bottom:1px solid #dbe3ed}header a{min-height:44px;display:inline-flex;align-items:center;gap:10px;color:#1e3a8a;text-decoration:none;font-weight:800}header b{display:grid;width:36px;height:36px;place-items:center;border-radius:12px;background:#1e3a8a;color:#fff}main{width:min(760px,100%);min-height:calc(100vh - 150px);margin:auto;padding:64px max(16px,env(safe-area-inset-right)) 64px max(16px,env(safe-area-inset-left));display:flex;flex-direction:column;justify-content:center;align-items:flex-start}h1{margin:8px 0 16px;font-size:clamp(2.4rem,10vw,4.6rem);line-height:1.05}p{max-width:58ch;line-height:1.65;color:#475569}.eyebrow{color:#1e3a8a;font-size:.75rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}nav{display:flex;flex-wrap:wrap;gap:10px}nav a{min-height:48px;padding:0 18px;display:inline-flex;align-items:center;border:1px solid #cbd5e1;border-radius:14px;background:#fff;color:#1e3a8a;text-decoration:none;font-weight:800}.primary{background:#1e3a8a;color:#fff}a:focus-visible{outline:3px solid #2563b8;outline-offset:3px}footer{border-top:1px solid #dbe3ed;border-bottom:0;color:#475569}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}";
}
