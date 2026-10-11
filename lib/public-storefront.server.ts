import "server-only";

import {
  DomainApiError,
  readDomainPublicProduct,
  readDomainPublicStorefront
} from "./internal-api-client.server";
import {
  parsePublicProductPage,
  parsePublicStorefront
} from "./public-storefront";

export async function readPublicStorefront(sellerSlug: string, cursor: string | null, limit = 12) {
  const parsed = parsePublicStorefront(await readDomainPublicStorefront(sellerSlug, cursor, limit));
  if (!parsed) throw new DomainApiError(502, "invalid_storefront_response");
  return parsed;
}

export async function readPublicProduct(sellerSlug: string, productSlug: string) {
  const parsed = parsePublicProductPage(await readDomainPublicProduct(sellerSlug, productSlug));
  if (!parsed) throw new DomainApiError(502, "invalid_storefront_response");
  return parsed;
}
