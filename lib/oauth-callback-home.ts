import type { DemoProfile } from "./demo-config";

// Para onde a pessoa cai quando não pediu destino nenhum.
//
// `hasStore` tem três valores de propósito. `false` é ausência comprovada de
// loja, e só ela manda para a biblioteca: quem só comprou um curso não deve
// ser recebido por "Seu negócio em um só lugar". `null` é indisponibilidade —
// a listagem não respondeu — e mantém o destino antigo, porque uma falha
// transitória de leitura não pode reclassificar um vendedor existente.
export function oauthCallbackHome(
  profile: DemoProfile | null,
  hasStore: boolean | null = null
) {
  if (profile?.home) return profile.home;
  return hasStore === false ? "/library" : "/dashboard";
}
