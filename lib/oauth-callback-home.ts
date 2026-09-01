import type { DemoProfile } from "./demo-config";

// Para onde a pessoa cai quando não pediu destino nenhum. Quem ainda não tem
// loja não tem painel de vendedor para ver: mandá-la para lá é apresentar
// "Seu negócio em um só lugar" a quem, na prática, comprou um curso. Quem
// entra para vender chega com destino explícito no `next`, vindo do
// "Vender agora", então este padrão não atrapalha o cadastro.
export function oauthCallbackHome(profile: DemoProfile | null, hasStore = false) {
  return profile?.home ?? (hasStore ? "/dashboard" : "/library");
}
