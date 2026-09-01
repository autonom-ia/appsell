// Quais permissões de escrita a sessão leva do login para a aplicação.
//
// Antes desta separação, todo mundo que não fosse comprador demo saía do
// callback com CSRF de criar, atualizar e publicar produto — inclusive um
// comprador real, que nunca teve loja. O dado seguia protegido pela RLS, mas o
// gate estava na camada errada.
//
// Onboarding e produto não andam juntos: o primeiro é o que permite criar a
// PRIMEIRA loja e precisa existir justamente para quem ainda não tem nenhuma;
// os de produto exigem membership provada.
export interface SellerCookiePlan {
  readonly onboarding: boolean;
  readonly product: boolean;
}

export interface SellerCookieInput {
  readonly demoPersona?: string | null;
  readonly demoSuperadmin: boolean;
  readonly hasSellerMembership: boolean;
}

export function sellerCookiePlan(input: SellerCookieInput): SellerCookiePlan {
  if (input.demoPersona === "buyer" || input.demoSuperadmin) {
    return { onboarding: false, product: false };
  }
  // A persona demo de admin não consulta a API de domínio: o runtime local
  // roda sem ela, e tirar o CSRF de produto ali quebraria o próprio demo.
  if (input.demoPersona) return { onboarding: true, product: true };
  return { onboarding: true, product: input.hasSellerMembership };
}
