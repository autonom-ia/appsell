import "server-only";

import { internalServiceConfig } from "@/lib/internal-api-client.server";

export class DescadastroIndisponivel extends Error {
  constructor() {
    super("unsubscribe_unavailable");
  }
}

// Chama a API com a credencial de serviço. A API responde 204 para token
// válido ou não; qualquer outra coisa é indisponibilidade. O token nunca vai
// para mensagem de erro.
export async function descadastrarPorToken(token: string): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3_000);
  try {
    const config = internalServiceConfig();
    const resposta = await fetch(new URL("/v1/public/email/unsubscribe", config.apiOrigin), {
      method: "POST",
      headers: {
        "x-appsell-service-credential": config.serviceCredentialEncoded,
        "content-type": "application/json"
      },
      body: JSON.stringify({ token }),
      cache: "no-store",
      signal: controller.signal
    });
    if (resposta.status !== 204) {
      registrarFalha("upstream_status", null, resposta.status);
      throw new DescadastroIndisponivel();
    }
  } catch (error) {
    if (error instanceof DescadastroIndisponivel) throw error;
    registrarFalha("upstream_unavailable", error, null);
    throw new DescadastroIndisponivel();
  } finally {
    clearTimeout(timeout);
  }
}

// Só o motivo, o NOME do erro e o status: a mensagem pode ecoar a URL, e o
// token nunca entra no log.
function registrarFalha(motivo: string, error: unknown, status: number | null): void {
  console.error(
    JSON.stringify({
      level: "error",
      marker: "appsell.email_unsubscribe.unavailable",
      reason: motivo,
      error_name: error instanceof Error ? error.name : null,
      upstream_status: status
    })
  );
}
