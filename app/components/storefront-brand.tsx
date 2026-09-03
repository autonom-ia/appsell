import type { CSSProperties, ReactNode } from "react";
import Link from "next/link";

import type { PublicStorefrontSeller } from "@/lib/public-storefront";
import { storeAccent, storeInitials } from "@/lib/store-brand";

// A marca da loja nas páginas públicas (#352). Três lugares mostram a mesma
// coisa — vitrine, página do produto, página de acesso — e um só componente
// evita que discordem. Sem logo, iniciais; sem cor legível, o tom padrão.

export function storefrontClassName(seller: PublicStorefrontSeller, extra = "") {
  const branded = storeAccent(seller.accent_color) ? " storefront-branded" : "";
  return `storefront-page${branded}${extra ? ` ${extra}` : ""}`;
}

/** A cor de destaque como variável de CSS, só quando passa na régua. */
export function storefrontStyle(seller: PublicStorefrontSeller): CSSProperties | undefined {
  const accent = storeAccent(seller.accent_color);
  return accent ? ({ "--store-accent": accent } as CSSProperties) : undefined;
}

export function StoreIdentity({ seller, size = "small" }: { seller: PublicStorefrontSeller; size?: "small" | "large" }) {
  const className = size === "large" ? "storefront-identity" : "storefront-identity storefront-identity-small";
  if (seller.logo) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        className={`${className} storefront-logo`}
        src={seller.logo.url}
        alt={`Logo de ${seller.name}`}
        width={seller.logo.width}
        height={seller.logo.height}
        decoding="async"
      />
    );
  }
  return <div className={className} aria-hidden="true">{storeInitials(seller.name)}</div>;
}

export function StorefrontTopbar({ seller, action }: { seller: PublicStorefrontSeller; action: ReactNode }) {
  return (
    <header className="storefront-topbar">
      <Link className="landing-brand storefront-brand" href={`/s/${seller.slug}`} aria-label={`${seller.name} — início da loja`}>
        <StoreIdentity seller={seller}/>
        <strong>{seller.name}</strong>
      </Link>
      {action}
    </header>
  );
}

export function StorefrontFooter() {
  return (
    <footer className="storefront-footer">
      <span>Loja criada com Autonom.ia Sell</span>
      <small>Pagamento e acesso protegidos pela plataforma.</small>
    </footer>
  );
}
