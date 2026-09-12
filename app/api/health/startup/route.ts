import { NextResponse } from "next/server";

import { readinessState } from "@/lib/readiness.server";

export const dynamic = "force-dynamic";

const HEADERS = {
  "cache-control": "no-store",
  "x-content-type-options": "nosniff"
} as const;

/**
 * Marca que esta task já serviu alguma vez. Vive no processo e não volta atrás.
 * Em `globalThis` para sobreviver ao recarregamento de módulo em
 * desenvolvimento; em produção o efeito é o de uma variável de módulo.
 */
const MARCA = Symbol.for("appsell.web.everReady");
const registro = globalThis as unknown as Record<symbol, boolean | undefined>;

/**
 * O endereço que o `HEALTHCHECK` do contêiner sonda.
 *
 * Responde a uma pergunta diferente da do `ready`: não "dá para me mandar
 * tráfego agora", e sim "esta task chegou a servir alguma vez".
 *
 * Enquanto a task nunca ficou pronta, espelha o `ready` — é o que impede a
 * promoção silenciosa de uma task quebrada num deploy. Depois que ficou pronta
 * uma vez, responde pelo processo e só por ele; degradação em regime é assunto
 * do `ready`, que segue devolvendo 503 e tirando a task de rotação.
 *
 * Em 12/09/2026 este contêiner sondava o `ready`, que consulta a API. A API
 * entrou em laço de reinício e derrubou o `web` junto, repetidamente, sem que
 * ele tivesse problema nenhum: servia catálogo e páginas públicas o tempo todo.
 */
export async function GET() {
  if (registro[MARCA] !== true) {
    const state = await readinessState();
    if (!state.ready) {
      return NextResponse.json(
        { status: "starting", ever_ready: false },
        { status: 503, headers: HEADERS }
      );
    }
    registro[MARCA] = true;
  }
  return NextResponse.json({ status: "ok", ever_ready: true }, { status: 200, headers: HEADERS });
}
