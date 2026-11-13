import { describe, expect, it, vi } from "vitest";

// A rota do Next importa `server-only`, que não existe fora do Next.
vi.mock("server-only", () => ({}));

import { mappedError } from "../app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { intentFailureCode, startFailure } from "../app/components/lesson-asset-panel";
import { DomainApiError } from "../lib/internal-api-client.server";

/**
 * O código de falha da intenção, lido do corpo que chega de verdade ao navegador.
 *
 * O navegador não fala com a API: fala com a rota do Next de `upload-intents`.
 * A API responde `{"detail": "storage_quota_exceeded"}`; `domainRequest` lê o
 * `detail` e lança `DomainApiError(409, detail)`; a rota devolve
 * `mappedError(erro)`. A resposta abaixo sai dessa mesma função, e não de um
 * corpo escrito à mão: um corpo inventado já escondeu o formato errado uma vez.
 */
function respostaDaRota(status: number, code: string): Response {
  return mappedError(new DomainApiError(status, code));
}

describe("limite de envio, do corpo da rota até a mensagem", () => {
  it("a cota cheia chega como 409 e vira a mensagem de espaço cheio", async () => {
    const resposta = respostaDaRota(409, "storage_quota_exceeded");
    expect(resposta.status).toBe(409);
    const codigo = await intentFailureCode(resposta);
    expect(codigo).toBe("intent_storage_quota_exceeded");
    expect(startFailure(new Error(codigo))[0]).toBe("O espaço de arquivos da loja está cheio.");
  });

  it("o 409 do curso que mudou continua sendo o curso que mudou", async () => {
    const codigo = await intentFailureCode(respostaDaRota(409, "draft_version_conflict"));
    expect(codigo).toBe("intent_409");
    expect(startFailure(new Error(codigo))[0]).toBe("O curso mudou em outra aba ou aparelho.");
  });

  it("o 429 vira envios demais sem ler o corpo", async () => {
    const resposta = respostaDaRota(429, "upload_limit_reached");
    expect(await intentFailureCode(resposta)).toBe("intent_429");
    expect(resposta.bodyUsed).toBe(false);
  });

  it("um 409 cujo corpo não termina cai no código genérico dentro do prazo", async () => {
    const semFim = new ReadableStream<Uint8Array>({ start() {} });
    const resposta = new Response(semFim, { status: 409 });
    expect(await intentFailureCode(resposta, 20)).toBe("intent_409");
  });

  it("um 409 sem JSON cai no código genérico", async () => {
    expect(await intentFailureCode(new Response("falhou", { status: 409 }))).toBe("intent_409");
  });
});
