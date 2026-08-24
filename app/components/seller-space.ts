export interface SellerSpace {
  id: string;
  slug: string;
  display_name: string;
  role: string;
}

const PLATFORM_SUPERADMIN_ROLE = "platform_superadmin";

export function sellerRoleLabel(role: string): string {
  if (role === "owner" || role === "seller_owner") return "proprietário";
  if (role === "seller_admin") return "administrador";
  if (role === PLATFORM_SUPERADMIN_ROLE) return "superadmin da plataforma";
  return "membro da equipe";
}

/**
 * Returns the only space that may be selected automatically for the current
 * identity, or `null` when the choice has to stay explicit.
 *
 * A seller can never onboard a second space by itself
 * (`app_private.onboard_seller` rejects a second active `seller_owner`
 * membership), so a single-space account has nothing to disambiguate. Accounts
 * that reach more than one space, and `platform_superadmin` in every case,
 * keep choosing the space by hand: those are exactly the cases where a silent
 * default could write into the wrong tenant.
 */
export function autoSelectableSpace(spaces: readonly SellerSpace[] | null): SellerSpace | null {
  if (!spaces || spaces.length !== 1) return null;
  const [space] = spaces;
  if (!space || space.role === PLATFORM_SUPERADMIN_ROLE) return null;
  return space;
}
