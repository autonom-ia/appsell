// Quem vê "Configurações" no menu (#352): só dono e admin da primeira loja. O
// papel vem da sessão, resolvido no servidor; a função do banco recusa de
// novo se alguém chamar a rota na mão.
export const STORE_SETTINGS_ROLES: ReadonlySet<string> = new Set(["seller_owner", "seller_admin"]);

export function canConfigureStore(
  sellers: readonly { readonly role: string }[] | null | undefined
): boolean {
  const first = sellers?.[0];
  return first !== undefined && STORE_SETTINGS_ROLES.has(first.role);
}
