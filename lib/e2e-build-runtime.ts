// O e2e roda sobre o build de produção, não sobre o `next dev`:
// o servidor de desenvolvimento travava ~30 s atendendo o WebKit e deixava a
// suíte instável (#585). A suíte entra como persona demo, que o build de
// produção recusa. Este runtime libera a demo nesse build só quando as três
// travas valem juntas, e produção não tem nenhuma delas:
//   1. NODE_ENV=production com APP_ENV=ci (produção usa APP_ENV=production);
//   2. origem pública https://localhost:3000 com OAuth local (produção usa
//      o domínio real e o Identity);
//   3. o marcador do job de e2e, que só o playwright.config.ts passa.
// tests/e2e-build-runtime.test.ts prova cada recusa; tests/production/
// prova, contra o mesmo build e sem o marcador, que a demo fica fechada.
export const E2E_BUILD_RUNTIME_MARKER = "appsell-e2e-build-v1";
export const E2E_BUILD_PUBLIC_ORIGIN = "https://localhost:3000";

export function e2eBuildRuntimeEnabled(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return environment.NODE_ENV === "production" &&
    environment.APP_ENV === "ci" &&
    environment.APPSELL_PUBLIC_ORIGIN === E2E_BUILD_PUBLIC_ORIGIN &&
    environment.APPSELL_OAUTH_MODE === "local" &&
    environment.APPSELL_E2E_BUILD_RUNTIME === E2E_BUILD_RUNTIME_MARKER;
}
