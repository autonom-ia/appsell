import type { Instrumentation } from "next";

import { instalarConsoleRedigido } from "./lib/console-redigido";
import { webRequestErrorLine } from "./lib/error-log";

// O Next imprime o erro cru (`⨯ Error: ...`, com mensagem e stack) pelo
// console, além de chamar o `onRequestError`. Envolver o console no arranque é
// o que impede o texto cru de sair (#549). Roda uma vez por runtime.
export function register(): void {
  instalarConsoleRedigido();
}

// Erro de rota, página ou server action do web vira uma linha JSON redigida
// (#549). Sem isto, o Next imprime o erro em texto, e o alerta do #472, que só
// conta linha JSON com `level` de erro, não o enxergava.
export const onRequestError: Instrumentation.onRequestError = (error, request, context) => {
  console.error(webRequestErrorLine(error, request, context));
};
