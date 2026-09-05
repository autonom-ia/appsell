export function productCheckoutLoginHref(productPath: string) {
  const params = new URLSearchParams({
    next: productPath,
    new_user: "true"
  });
  return `/auth/login?${params.toString()}`;
}
