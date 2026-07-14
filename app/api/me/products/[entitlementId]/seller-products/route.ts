import { NextRequest } from "next/server";
import { buyerSellerProductsResponse } from "../../../../library/_route-shared";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ entitlementId: string }> }
) {
  const { entitlementId } = await context.params;
  return buyerSellerProductsResponse(request, entitlementId);
}
