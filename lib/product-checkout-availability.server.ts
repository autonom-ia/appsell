import "server-only";

import { resolveCheckoutProduct } from "./checkout-config";
import { checkoutProvider } from "./financial-external-checkout.server";

export function isProductCheckoutAvailable(sellerSlug: string, productSlug: string) {
  return checkoutProvider() === "financial_checkout" ||
    resolveCheckoutProduct(sellerSlug, productSlug) !== null;
}
