import "server-only";

import { internalServiceConfig } from "@/lib/internal-api-client.server";

const TEMPO_MAXIMO_MS = 2_000;

export class RastreioIndisponivel extends Error {
  constructor() {
    super("tracking_unavailable");
  }
}

// Chama a rota pública de rastreio da API com a credencial de serviço. A API
// responde 204 para qualquer pedido válido; outra resposta é indisponibilidade.
export async function chamarRastreio(caminho: string, corpo: Record<string, unknown>): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TEMPO_MAXIMO_MS);
  try {
    const config = internalServiceConfig();
    const resposta = await fetch(new URL(caminho, config.apiOrigin), {
      method: "POST",
      headers: {
        "x-appsell-service-credential": config.serviceCredentialEncoded,
        "content-type": "application/json"
      },
      body: JSON.stringify(corpo),
      cache: "no-store",
      signal: controller.signal
    });
    if (resposta.status !== 204) {
      registrarFalha(caminho, "upstream_status", null, resposta.status);
      throw new RastreioIndisponivel();
    }
  } catch (error) {
    if (error instanceof RastreioIndisponivel) throw error;
    registrarFalha(caminho, "upstream_unavailable", error, null);
    throw new RastreioIndisponivel();
  } finally {
    clearTimeout(timeout);
  }
}

// Só o caminho, o motivo, o NOME do erro e o status: nada do corpo, que leva a
// referência da compra.
export function registrarFalha(
  caminho: string,
  motivo: string,
  error: unknown,
  status: number | null
): void {
  // Nunca lança: quem chama está no caminho do checkout ou de uma resposta
  // pública, e uma falha do log não pode virar falha da requisição.
  try {
    console.error(
      JSON.stringify({
        level: "error",
        marker: "appsell.tracking.unavailable",
        path: caminho,
        reason: motivo,
        error_name: nomeDoErro(error),
        upstream_status: status
      })
    );
  } catch {
    // Sem saída para o log, não resta o que fazer além de seguir.
  }
}

function nomeDoErro(error: unknown): string | null {
  try {
    return error instanceof Error && typeof error.name === "string" ? error.name : null;
  } catch {
    return null;
  }
}
