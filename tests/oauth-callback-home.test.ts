import { describe, expect, it } from "vitest";

import type { DemoProfile } from "../lib/demo-config";
import { oauthCallbackHome } from "../lib/oauth-callback-home";

function profile(persona: DemoProfile["persona"], home: DemoProfile["home"]): DemoProfile {
  return {
    persona,
    home,
    subject: `${persona}-subject`,
    email: `${persona}@example.test`,
    label: persona
  };
}

describe("OAuth callback home", () => {
  it("keeps a real identity that already has a store on the real dashboard", () => {
    // O superadmin de plataforma entra aqui: desde a 0032 ele recebe as lojas
    // ativas projetadas, então chega com loja.
    expect(oauthCallbackHome(null, true)).toBe("/dashboard");
  });

  it("sends a real identity without a store to the library", () => {
    // Mudou com a #363. O painel do vendedor prometia "Seu negócio em um só
    // lugar" a quem só tinha comprado um curso, com um botão "Criar produto"
    // que daria erro. Quem entra para vender chega com destino explícito no
    // `next`, então este padrão não atrapalha o cadastro.
    expect(oauthCallbackHome(null, false)).toBe("/library");
  });

  it("mantém o dashboard quando a ausência de loja não foi comprovada", () => {
    // Sem argumento, ou com null, o estado é "não sei": uma falha transitória
    // da listagem não pode reclassificar um vendedor existente.
    expect(oauthCallbackHome(null)).toBe("/dashboard");
    expect(oauthCallbackHome(null, null)).toBe("/dashboard");
  });

  it("preserves the isolated demo persona destinations", () => {
    expect(oauthCallbackHome(profile("admin", "/dashboard"))).toBe("/dashboard");
    expect(oauthCallbackHome(profile("buyer", "/library"))).toBe("/library");
    expect(oauthCallbackHome(profile("superadmin", "/platform/demo-inspection")))
      .toBe("/platform/demo-inspection");
  });
});
