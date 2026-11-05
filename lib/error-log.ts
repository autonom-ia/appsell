import { redigirTexto } from "./redacao";

// Uma linha por erro de requisição do web (#549), no formato que o alerta do
// #472 lê. Leva o **template** da rota (`/s/[sellerSlug]`), nunca o caminho
// concreto, a query ou os cabeçalhos. Mensagem e stack passam pelo redator,
// porque ninguém escolhe o que vai dentro deles.
const MAX_MENSAGEM = 500;
const MAX_STACK = 4_000;

type Pedido = { readonly method?: string };
type Contexto = { readonly routePath?: string; readonly routeType?: string; readonly routerKind?: string };

export function webRequestErrorLine(error: unknown, request: Pedido, context: Contexto): string {
  const erro = error instanceof Error ? error : null;
  const digest = typeof (error as { digest?: unknown } | null)?.digest === "string"
    ? (error as { digest: string }).digest
    : null;
  return JSON.stringify({
    level: "error",
    marker: "appsell.web.request_error",
    method: typeof request.method === "string" ? request.method : null,
    route_path: typeof context.routePath === "string" ? redigirTexto(context.routePath) : null,
    route_type: context.routeType ?? null,
    router_kind: context.routerKind ?? null,
    error_type: erro?.name ?? typeof error,
    message: redigirTexto(erro?.message ?? String(error)).slice(0, MAX_MENSAGEM),
    stack: erro?.stack ? redigirTexto(erro.stack).slice(0, MAX_STACK) : null,
    digest
  });
}
