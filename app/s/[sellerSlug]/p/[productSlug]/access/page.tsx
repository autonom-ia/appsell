import { cookies } from "next/headers";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { FreeAccessWorkspace } from "@/app/components/free-access-workspace";
import { sessionCookieName } from "@/lib/cookies";
import { DomainApiError, readPublicStorefront } from "@/lib/internal-api-client.server";
import { ENROLL_PUBLIC_PRODUCT_CSRF_ACTION, csrfTokenForSession } from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { parsePublicStorefront } from "@/lib/public-storefront";

export const dynamic = "force-dynamic";

export default async function FreeAccessPage({
  params
}: {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}) {
  const { sellerSlug, productSlug } = await params;
  const storefront = await loadStorefront(sellerSlug);
  const product = storefront.products.find((item) => item.slug === productSlug);
  if (!product || (product.access_mode ?? "paid") !== "free") notFound();

  const accessPath = `/s/${storefront.seller.slug}/p/${product.slug}/access`;
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = await oauthService().getSession(sessionId);
  if (!sessionId || !session) {
    redirect(`/auth/login?next=${encodeURIComponent(accessPath)}`);
  }
  const csrf = csrfTokenForSession(sessionId, ENROLL_PUBLIC_PRODUCT_CSRF_ACTION);

  return <div className="storefront-page product-checkout-page">
    <a className="skip-link" href="#free-access-content">Pular para o acesso</a>
    <header className="storefront-topbar">
      <Link className="landing-brand" href={`/s/${storefront.seller.slug}`} aria-label={`${storefront.seller.name} — voltar à loja`}>
        <span className="brand-mark" aria-hidden="true">a</span>
        <strong>Autonom.ia <span>Sell</span></strong>
      </Link>
      <Link className="storefront-login" href="/library">Minha biblioteca</Link>
    </header>
    <main id="free-access-content" tabIndex={-1} className="product-checkout-main">
      <section className="product-checkout-summary" aria-labelledby="free-access-title">
        <p className="eyebrow">{storefront.seller.name}</p>
        <h1 id="free-access-title">{product.title}</h1>
        {product.description.trim() ? <p>{product.description}</p> : null}
        <p className="product-checkout-price">Acesso gratuito</p>
      </section>
      <section className="product-checkout-action" aria-label="Liberar acesso">
        <FreeAccessWorkspace csrf={csrf} sellerId={storefront.seller.id} productId={product.id} title={product.title}/>
      </section>
    </main>
  </div>;
}

async function loadStorefront(sellerSlug: string) {
  try {
    const parsed = parsePublicStorefront(await readPublicStorefront(sellerSlug));
    if (!parsed) throw new DomainApiError(503, "invalid_public_storefront");
    return parsed;
  } catch (error) {
    if (error instanceof DomainApiError && error.status === 404) notFound();
    throw error;
  }
}
