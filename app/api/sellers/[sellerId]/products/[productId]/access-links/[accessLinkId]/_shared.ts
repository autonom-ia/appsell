const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export interface AccessLinkRouteContext {
  params: Promise<{ sellerId: string; productId: string; accessLinkId: string }>;
}

export async function accessLinkRouteIds(context: AccessLinkRouteContext) {
  const { sellerId, productId, accessLinkId } = await context.params;
  return UUID.test(sellerId) && UUID.test(productId) && UUID.test(accessLinkId)
    ? { sellerId, productId, accessLinkId }
    : null;
}
