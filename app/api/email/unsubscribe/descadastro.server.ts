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
    if (resposta.status !== 204) throw new DescadastroIndisponivel();
  } catch {
    throw new DescadastroIndisponivel();
  } finally {
    clearTimeout(timeout);
  }
}
