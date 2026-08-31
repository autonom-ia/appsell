import { MAX_VIDEO_BYTES as MAX_ASSET_VIDEO_BYTES } from "@/lib/asset-media";
import { NextRequest, NextResponse } from "next/server";

import { readDomainPdfLinkVideo } from "@/lib/internal-api-client.server";
import { safeVideoRange } from "@/lib/protected-pdf-links.server";
import {
  authenticatedLearningSession,
  learningAssetLinkIds,
  learningError,
  learningNotFound,
  learningUnauthorized,
  type LearningAssetLinkRouteContext
} from "../../../../../../../_route-shared";
import {
  configuredStorageUploadOrigin,
  trustedStorageDownloadUrl
} from "@/lib/storage-upload-policy.server";

/**
 * O vídeo referenciado dentro do PDF, num formato que o navegador toca.
 *
 * Antes esta rota ia buscar o arquivo original na origem externa e o repassava
 * como viesse. Os vídeos do vendedor são QuickTime, e o Chrome recusa
 * `video/quicktime` — `canPlayType` devolve string vazia. O aluno via "Não foi
 * possível reproduzir este vídeo" por causa do container do arquivo, que não é
 * assunto dele.
 *
 * Agora quem busca a origem é o domínio, que tem `ffmpeg` e prepara um MP4
 * equivalente uma vez por endereço. Duas consequências além de o vídeo tocar:
 * o BFF deixou de fazer requisições para fora, e o que ele serve vem do nosso
 * próprio storage, com `Range` de verdade — a barra de progresso funciona.
 */
// O mesmo teto do catálogo de mídia, onde `video/mp4` sempre valeu 100 MiB.
// Eram 512 aqui, número que nasceu no trabalho de vídeo do PDF sem olhar o
// catálogo: o mesmo conteúdo tinha dois tetos, cinco vezes distantes,
// dependendo de ter sido enviado pelo vendedor ou citado dentro do PDF.
const MAX_VIDEO_BYTES = MAX_ASSET_VIDEO_BYTES;
const DERIVATIVE_MEDIA_TYPE = "video/mp4";

export async function GET(request: NextRequest, context: LearningAssetLinkRouteContext) {
  return proxyPdfVideo(request, context);
}

export async function HEAD(request: NextRequest, context: LearningAssetLinkRouteContext) {
  return proxyPdfVideo(request, context);
}

async function proxyPdfVideo(request: NextRequest, context: LearningAssetLinkRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetLinkIds(context);
    if (!ids) return learningNotFound();
    const requestedRange = safeVideoRange(request.headers.get("range"));
    if (requestedRange === undefined) return invalidRange();

    const prepared = preparedVideo(await readDomainPdfLinkVideo(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.assetId,
      ids.linkId
    ));
    if (prepared === null) return videoUnavailable();
    if (prepared.status === "preparing") return videoPreparing(prepared.retryAfterSeconds);

    const target = trustedDerivative(prepared.downloadUrl, prepared.expiresInSeconds);
    if (target === null) return videoUnavailable();

    // O endereço assinado vale para `GET` — o método entra na assinatura, e um
    // `HEAD` nele volta como assinatura inválida. Então a sonda vira um `GET`
    // do primeiro byte: o storage responde com o `content-range`, que carrega o
    // tamanho total, e nada além de um byte trafega.
    const probing = request.method === "HEAD";
    const upstreamRange = probing ? "bytes=0-0" : requestedRange;
    const upstream = await fetch(target, {
      method: "GET",
      headers: upstreamRange ? { range: upstreamRange } : undefined,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10 * 60 * 1_000)
    });
    const contentRange = upstream.headers.get("content-range");
    const contentLength = probing
      ? totalFromProbe(contentRange)
      : Number(upstream.headers.get("content-length"));
    const usable = [200, 206].includes(upstream.status)
      && validContentRange(upstream.status, contentRange)
      && Number.isSafeInteger(contentLength) && contentLength !== null
      && contentLength >= 1 && contentLength <= MAX_VIDEO_BYTES;
    // A sonda leu um byte e o descarta; uma resposta inutil tambem e descartada,
    // em vez de deixar a conexao com o storage aberta ate o coletor agir.
    if (probing || !usable) await upstream.body?.cancel().catch(() => undefined);
    if (!usable) return videoUnavailable();
    return new NextResponse(probing ? null : upstream.body, {
      // A sonda descreve o recurso inteiro, não o byte que foi buscado.
      status: probing ? 200 : upstream.status,
      headers: {
        "accept-ranges": "bytes",
        "cache-control": "private, no-store, max-age=0",
        "content-disposition": 'inline; filename="video-protegido.mp4"',
        "content-length": String(contentLength),
        // O derivado é sempre MP4. O tipo do arquivo de origem não chega aqui,
        // e é por isso que o navegador consegue tocar.
        "content-type": DERIVATIVE_MEDIA_TYPE,
        "cross-origin-resource-policy": "same-origin",
        pragma: "no-cache",
        "referrer-policy": "no-referrer",
        "surrogate-control": "no-store",
        vary: "Cookie, Range",
        "x-content-type-options": "nosniff",
        ...(contentRange && !probing ? { "content-range": contentRange } : {})
      }
    });
  } catch (error) {
    return learningError(error);
  }
}

type PreparedVideo =
  | { status: "preparing"; retryAfterSeconds: number }
  | { status: "ready"; downloadUrl: string; expiresInSeconds: number };

/** A resposta do domínio, conferida antes de virar uma requisição. */
function preparedVideo(payload: unknown): PreparedVideo | null {
  if (!isRecord(payload) || !hasExactKeys(payload, ["video"]) || !isRecord(payload.video)) {
    return null;
  }
  const video = payload.video;
  if (video.status === "preparing") {
    if (!hasExactKeys(video, ["retry_after_seconds", "status"])) return null;
    const retry = Number(video.retry_after_seconds);
    if (!Number.isSafeInteger(retry) || retry < 1 || retry > 60) return null;
    return { status: "preparing", retryAfterSeconds: retry };
  }
  if (video.status !== "ready" || !hasExactKeys(video, [
    "byte_size", "download_url", "expires_in_seconds", "media_type", "status"
  ])) return null;
  if (video.media_type !== DERIVATIVE_MEDIA_TYPE) return null;
  const byteSize = Number(video.byte_size);
  if (!Number.isSafeInteger(byteSize) || byteSize < 1 || byteSize > MAX_VIDEO_BYTES) return null;
  if (typeof video.download_url !== "string") return null;
  const expires = Number(video.expires_in_seconds);
  if (!Number.isSafeInteger(expires) || expires < 1) return null;
  return { status: "ready", downloadUrl: video.download_url, expiresInSeconds: expires };
}

/**
 * O endereço assinado tem de ser do nosso storage, e de mais lugar nenhum.
 *
 * O domínio é confiável, mas esta é a última fronteira antes de o servidor
 * seguir um endereço: conferir aqui custa nada e fecha a porta para um alvo
 * inesperado chegar por dentro.
 */
function trustedDerivative(value: string, expires: number) {
  try {
    const storageOrigin = configuredStorageUploadOrigin();
    if (!trustedStorageDownloadUrl(value, expires, storageOrigin)) return null;
    return new URL(value);
  } catch {
    return null;
  }
}

/**
 * O tamanho total que o storage declarou, e só para a sonda.
 *
 * Exige exatamente o primeiro byte: aceitar qualquer trecho deixaria uma
 * resposta que não é a que foi pedida passar por descrição do recurso.
 */
function totalFromProbe(value: string | null) {
  const total = Number(value?.match(/^bytes 0-0\/(\d+)$/)?.[1]);
  return Number.isSafeInteger(total) ? total : null;
}

function validContentRange(status: number, value: string | null) {
  if (status === 200) return value === null;
  if (value === null) return false;
  const match = value.match(/^bytes (\d+)-(\d+)\/(\d+)$/);
  if (!match) return false;
  const start = Number(match[1]);
  const end = Number(match[2]);
  const total = Number(match[3]);
  return Number.isSafeInteger(start) && Number.isSafeInteger(end) && Number.isSafeInteger(total) &&
    start >= 0 && end >= start && end < total && total <= MAX_VIDEO_BYTES;
}

function invalidRange() {
  return NextResponse.json({ error: "invalid_video_range" }, {
    status: 416,
    headers: { "cache-control": "private, no-store" }
  });
}

/**
 * O vídeo está sendo preparado, e vale tentar de novo.
 *
 * Diferente de `503`: ali não há o que esperar. Aqui há, e o `retry-after` diz
 * quanto — é o que permite à tela mostrar "preparando" em vez de "não foi
 * possível".
 */
function videoPreparing(retryAfterSeconds: number) {
  return NextResponse.json({ error: "protected_video_preparing", retryAfterSeconds }, {
    status: 202,
    headers: {
      "cache-control": "private, no-store",
      "retry-after": String(retryAfterSeconds)
    }
  });
}

function videoUnavailable() {
  return NextResponse.json({ error: "protected_video_unavailable" }, {
    status: 503,
    headers: { "cache-control": "private, no-store" }
  });
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]) {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length && actual.every((key, index) => key === sorted[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
