import type { Instrumentation } from "next";

import { webRequestErrorLine } from "./lib/error-log";

// Erro de rota, página ou server action do web vira uma linha JSON redigida
// (#549). Sem isto, o Next imprime o erro em texto, e o alerta do #472, que só
// conta linha JSON com `level` de erro, não o enxergava.
export const onRequestError: Instrumentation.onRequestError = (error, request, context) => {
  console.error(webRequestErrorLine(error, request, context));
};
