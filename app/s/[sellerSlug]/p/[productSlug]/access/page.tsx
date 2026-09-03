import { cookies } from "next/headers";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { FreeAccessWorkspace } from "@/app/components/free-access-workspace";
import { StorefrontFooter, StorefrontTopbar, storefrontClassName, storefrontStyle } from "@/app/components/storefront-brand";
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
  const seller = storefront.seller;
  const product = storefront.products.find((item) => item.slug === productSlug);
  if (!product || (product.access_mode ?? "paid") !== "free") notFound();

  const accessPath = `/s/${seller.slug}/p/${product.slug}/access`;
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = await oauthService().getSession(sessionId);
  if (!sessionId || !session) {
    redirect(`/auth/login?next=${encodeURIComponent(accessPath)}`);
  }
  const csrf = csrfTokenForSession(sessionId, ENROLL_PUBLIC_PRODUCT_CSRF_ACTION);

  return <div className={storefrontClassName(seller, "product-checkout-page")} style={storefrontStyle(seller)}>
    <a className="skip-link" href="#free-access-content">Pular para o acesso</a>
    <StorefrontTopbar seller={seller} action={<Link className="storefront-login" href="/library">Minha biblioteca</Link>}/>
    <main id="free-access-content" tabIndex={-1} className="product-checkout-main">
      <section className="product-checkout-summary" aria-labelledby="free-access-title">
        <p className="eyebrow">{seller.name}</p>
        <h1 id="free-access-title">{product.headline ?? product.title}</h1>
        {product.headline ? <p className="lp-title">{product.title}</p> : null}
        {product.description.trim() ? <p>{product.description}</p> : null}
        <p className="product-checkout-price">Acesso gratuito</p>
      </section>
      <section className="product-checkout-action" aria-label="Liberar acesso">
        <FreeAccessWorkspace csrf={csrf} sellerId={seller.id} productId={product.id} title={product.title}/>
      </section>
    </main>
    <StorefrontFooter/>
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
