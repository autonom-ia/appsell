import { describe, expect, it } from "vitest";
import { sellerCookiePlan } from "../lib/seller-cookie-plan";

describe("permissões que a sessão leva do login", () => {
  it("não dá permissão de produto a quem não tem loja", () => {
    // O comprador real: identidade válida, zero membership. Ele saía do login
    // com CSRF de criar e publicar produto.
    expect(sellerCookiePlan({ demoSuperadmin: false, hasSellerMembership: false }))
      .toEqual({ onboarding: true, product: false });
  });

  it("mantém a permissão de onboarding para quem ainda não tem loja", () => {
    // Sem ela, quem se cadastra pelo "Vender agora" não consegue criar a
    // primeira loja: o cadastro vira um beco sem saída.
    expect(sellerCookiePlan({ demoSuperadmin: false, hasSellerMembership: false }).onboarding)
      .toBe(true);
  });

  it("dá permissão de produto a quem tem membership", () => {
    expect(sellerCookiePlan({ demoSuperadmin: false, hasSellerMembership: true }))
      .toEqual({ onboarding: true, product: true });
  });

  it("nega tudo ao comprador demo e ao superadmin demo", () => {
    expect(sellerCookiePlan({ demoPersona: "buyer", demoSuperadmin: false, hasSellerMembership: true }))
      .toEqual({ onboarding: false, product: false });
    expect(sellerCookiePlan({ demoPersona: "superadmin", demoSuperadmin: true, hasSellerMembership: true }))
      .toEqual({ onboarding: false, product: false });
  });

  it("preserva o demo de admin, que roda sem API de domínio", () => {
    expect(sellerCookiePlan({ demoPersona: "admin", demoSuperadmin: false, hasSellerMembership: false }))
      .toEqual({ onboarding: true, product: true });
  });
});
