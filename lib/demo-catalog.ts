import type { CatalogProduct, Entitlement } from "./catalog-projection";

export const DEMO_SELLER_ID = "seller-autonomia-lab";
export const DEMO_BUYER_SUB = "demo-buyer";

export const demoProducts: readonly CatalogProduct[] = [
  {
    id: "product-creator-os",
    sellerOrganizationId: DEMO_SELLER_ID,
    sellerName: "Autonom.ia Lab",
    sellerSlug: "autonomia-lab",
    slug: "creator-os",
    title: "Creator OS",
    description: "Da ideia ao produto digital com um sistema operacional de criação.",
    status: "published",
    priceInCents: 14900,
    accent: "blue",
    lessonCount: 24,
  },
  {
    id: "product-ai-playbook",
    sellerOrganizationId: DEMO_SELLER_ID,
    sellerName: "Autonom.ia Lab",
    sellerSlug: "autonomia-lab",
    slug: "ai-playbook",
    title: "AI Playbook",
    description: "Playbooks práticos para operar uma empresa AI-first.",
    status: "published",
    priceInCents: 8900,
    accent: "cyan",
    lessonCount: 18,
  },
  {
    id: "product-launch-lab",
    sellerOrganizationId: DEMO_SELLER_ID,
    sellerName: "Autonom.ia Lab",
    sellerSlug: "autonomia-lab",
    slug: "launch-lab",
    title: "Launch Lab",
    description: "Rascunho da próxima jornada de lançamento.",
    status: "draft",
    priceInCents: 0,
    accent: "orange",
    lessonCount: 7,
  },
  {
    id: "product-growth-systems",
    sellerOrganizationId: "seller-growth-partner",
    sellerName: "Growth Partner",
    sellerSlug: "growth-partner",
    slug: "growth-systems",
    title: "Growth Systems",
    description: "Aquisição e retenção orientadas por experimentos.",
    status: "published",
    priceInCents: 11900,
    accent: "orange",
    lessonCount: 16,
  },
];
export const demoEntitlements: readonly Entitlement[] = [
  {
    buyerIdentitySub: DEMO_BUYER_SUB,
    sellerOrganizationId: DEMO_SELLER_ID,
    productId: "product-creator-os",
    status: "active",
    progressPercent: 42,
  },
  {
    buyerIdentitySub: DEMO_BUYER_SUB,
    sellerOrganizationId: "seller-growth-partner",
    productId: "product-growth-systems",
    status: "active",
    progressPercent: 8,
  },
];
