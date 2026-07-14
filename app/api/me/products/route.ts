import { NextRequest } from "next/server";
import { buyerProductsResponse } from "../../library/_route-shared";

export async function GET(request: NextRequest) {
  return buyerProductsResponse(request);
}
