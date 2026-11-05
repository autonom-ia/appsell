import "server-only";

import { resolveCheckoutProduct } from "./checkout-config";
import { configuredCheckoutProvider } from "./financial-external-checkout.server";

// Without a valid provider (#544) the page offers no checkout at all.
export function isProductCheckoutAvailable(sellerSlug: string, productSlug: string) {
  const provider = configuredCheckoutProvider();
  if (provider === "financial_checkout") return true;
  return provider === "local_stripe" && resolveCheckoutProduct(sellerSlug, productSlug) !== null;
}
