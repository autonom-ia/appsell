import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

/**
 * O vídeo do PDF, entregue num formato que o navegador toca.
 *
 * Esta rota buscava o arquivo na origem externa e o repassava como viesse. Os
 * vídeos do vendedor são QuickTime, e o Chrome recusa `video/quicktime` —
 * `canPlayType` devolve string vazia. O aluno via "Não foi possível reproduzir
 * este vídeo" por causa do container do arquivo.
 *
 * Agora quem busca a origem e prepara o MP4 é o domínio, que tem `ffmpeg`. Esta
 * rota conversa com ele e serve o derivado do nosso próprio storage. Os testes
 * cobrem as três respostas possíveis — pronto, preparando, indisponível — e a
 * fronteira que impede o endereço assinado de apontar para fora.
 */

vi.mock("server-only", () => ({}));

const { getSession, readDomainPdfLinkVideo, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return { getSession: vi.fn(), readDomainPdfLinkVideo: vi.fn(), DomainApiError };
});

vi.mock("@/lib/internal-api-client.server", () => ({ readDomainPdfLinkVideo, DomainApiError }));
vi.mock("@/lib/oauth-service.server", () => ({ oauthService: () => ({ getSession }) }));

import { GET, HEAD } from "../app/api/library/items/[entitlementId]/assets/[assetId]/pages/links/[linkId]/media/route";

const ORIGIN = "http://localhost:3000";
const STORAGE_ORIGIN = "https://storage.example.test";
const ENTITLEMENT_ID = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const ASSET_ID = "a6f01626-8b83-55e5-becc-fbab785f1493";
const LINK_ID = "fedcba9876543210";
const DERIVATIVE = `${STORAGE_ORIGIN}/appsell-ready/derivatives/pdf-link-video/ab/${"a".repeat(64)}.mp4`
  + "?X-Amz-Signature=assinatura";

const context = {
  params: Promise.resolve({ entitlementId: ENTITLEMENT_ID, assetId: ASSET_ID, linkId: LINK_ID })
};

function ready(overrides: Record<string, unknown> = {}) {
  return {
    video: {
      status: "ready",
      download_url: DERIVATIVE,
      expires_in_seconds: 60,
      byte_size: 5_203_696,
      media_type: "video/mp4",
      ...overrides
    }
  };
}

function request({ cookie = true, range = "bytes=0-1023", method = "GET" } = {}) {
  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = "appsell_session=session-1";
  if (range) headers.range = range;
  return new NextRequest(
    `${ORIGIN}/api/library/items/${ENTITLEMENT_ID}/assets/${ASSET_ID}/pages/links/${LINK_ID}/media`,
    { headers, method }
  );
}

function upstream({ status = 206, length = 1024, range = "bytes 0-1023/5203696" } = {}) {
  return new Response(new Uint8Array(4), {
    status,
    headers: {
      "content-type": "video/mp4",
      "content-length": String(length),
      ...(range ? { "content-range": range } : {})
    }
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  process.env.APPSELL_STORAGE_PUBLIC_ORIGIN = STORAGE_ORIGIN;
  getSession.mockResolvedValue({ id: "session-1", subject: "aluno", tokenRevision: 1 });
  fetchMock = vi.fn().mockResolvedValue(upstream());
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("o vídeo do PDF chega ao aluno como MP4", () => {
  it("serve o derivado com o tipo que o navegador toca, e não o da origem", async () => {
    readDomainPdfLinkVideo.mockResolvedValue(ready());
    const response = await GET(request(), context);
    expect(response.status).toBe(206);
    // A origem é QuickTime; o que sai daqui nunca é.
    expect(response.headers.get("content-type")).toBe("video/mp4");
    expect(response.headers.get("accept-ranges")).toBe("bytes");
    expect(response.headers.get("content-range")).toBe("bytes 0-1023/5203696");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("repassa o intervalo pedido, para a barra de progresso funcionar", async () => {
    readDomainPdfLinkVideo.mockResolvedValue(ready());
    await GET(request({ range: "bytes=2048-4095" }), context);
    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers).toEqual({ range: "bytes=2048-4095" });
    expect(options.redirect).toBe("error");
  });

  it("responde 202 enquanto o servidor prepara, com o intervalo de nova tentativa", async () => {
    // Um 503 aqui seria mentira: há o que esperar, e é o que permite à tela
    // dizer "preparando" em vez de "não foi possível".
    readDomainPdfLinkVideo.mockResolvedValue({
      video: { status: "preparing", retry_after_seconds: 3 }
    });
    const response = await GET(request(), context);
    expect(response.status).toBe(202);
    expect(response.headers.get("retry-after")).toBe("3");
    expect(await response.json()).toEqual({
      error: "protected_video_preparing",
      retryAfterSeconds: 3
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("HEAD responde exatamente o que GET responderia, sem corpo", async () => {
    readDomainPdfLinkVideo.mockResolvedValue(ready());
    const response = await HEAD(request({ range: "", method: "HEAD" }), context);
    expect(response.status).toBe(206);
    expect(response.headers.get("content-type")).toBe("video/mp4");
    expect(await response.text()).toBe("");
    // O método chega até a origem: uma sonda não pode custar o vídeo inteiro.
    expect(fetchMock.mock.calls[0][1].method).toBe("HEAD");
  });
});

describe("o que a rota recusa antes de buscar qualquer coisa", () => {
  it("recusa sessão anônima", async () => {
    getSession.mockResolvedValue(null);
    expect((await GET(request({ cookie: false }), context)).status).toBe(401);
    expect(readDomainPdfLinkVideo).not.toHaveBeenCalled();
  });

  it("recusa um intervalo múltiplo", async () => {
    readDomainPdfLinkVideo.mockResolvedValue(ready());
    const response = await GET(request({ range: "bytes=0-1,5-9" }), context);
    expect(response.status).toBe(416);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("recusa um endereço assinado que não é do nosso storage", async () => {
    // Última fronteira antes de o servidor seguir um endereço. O domínio é
    // confiável, mas confiar sem conferir é como o alvo inesperado entra.
    readDomainPdfLinkVideo.mockResolvedValue(
      ready({ download_url: "https://outro.example.test/video.mp4?X-Amz-Signature=x" })
    );
    const response = await GET(request(), context);
    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["mídia que não é o derivado", ready({ media_type: "video/quicktime" })],
    ["tamanho fora da faixa", ready({ byte_size: 0 })],
    // A janela do endereço assinado é curta de propósito: o BFF busca na hora,
    // e um `expires` largo seria um endereço utilizável por muito mais tempo
    // do que o necessário.
    ["janela de assinatura longa demais", ready({ expires_in_seconds: 900 })],
    ["campo a mais", { video: { ...ready().video, extra: 1 } }],
    ["campo a menos", { video: { status: "ready", download_url: DERIVATIVE } }],
    ["estado desconhecido", { video: { status: "quase", retry_after_seconds: 3 } }],
    ["envelope errado", { asset: {} }],
    ["resposta vazia", null]
  ])("recusa resposta do domínio com %s", async (_nome, payload) => {
    readDomainPdfLinkVideo.mockResolvedValue(payload);
    expect((await GET(request(), context)).status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("recusa quando a origem devolve algo que não é um trecho válido", async () => {
    readDomainPdfLinkVideo.mockResolvedValue(ready());
    fetchMock.mockResolvedValue(upstream({ status: 206, range: "bytes 0-1023/0" }));
    expect((await GET(request(), context)).status).toBe(503);
  });

  it("recusa quando a origem falha", async () => {
    readDomainPdfLinkVideo.mockResolvedValue(ready());
    fetchMock.mockResolvedValue(upstream({ status: 404, range: "" }));
    expect((await GET(request(), context)).status).toBe(503);
  });
});
