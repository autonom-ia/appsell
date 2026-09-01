// O que a Visão geral pode afirmar sobre quem está olhando.
//
// Três estados, não dois: "tem loja", "comprovadamente não tem" e "ainda não
// sei". Tratar o terceiro como o segundo faz o painel anunciar "Comece criando
// sua loja" para um vendedor cuja sessão apenas demorou a resolver.
export type DashboardView = "loading" | "failed" | "demo" | "real";

export interface DashboardHeader {
  readonly activation: boolean;
  readonly cta: boolean;
}

export function dashboardHeader(view: DashboardView, hasStore: boolean | null): DashboardHeader {
  // O demo demonstra a jornada do vendedor inteira, inclusive criar produto.
  if (view === "demo") return { activation: false, cta: true };
  // Só afirma a ausência de loja quando ela foi comprovada.
  if (view === "real") return { activation: hasStore === false, cta: hasStore === true };
  return { activation: false, cta: false };
}
