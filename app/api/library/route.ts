import { NextRequest } from "next/server";
import { buyerProductsResponse } from "./_route-shared";

export async function GET(request: NextRequest) {
  return buyerProductsResponse(request);
}
