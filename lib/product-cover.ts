/**
 * O que o navegador precisa saber sobre uma capa antes de mandá-la.
 *
 * A checagem aqui não é a que protege nada — quem prova que o arquivo é uma
 * imagem é o verificador do servidor, abrindo os bytes. Esta serve para o
 * vendedor descobrir em um segundo, e não em quinze, que escolheu o arquivo
 * errado: recusar 40 MiB antes de subir 40 MiB é a diferença entre um aviso e
 * uma espera.
 *
 * Por isso as regras aqui são deliberadamente as mesmas do servidor. Se elas
 * divergirem, o navegador aceita algo que o servidor depois recusa, e o erro
 * aparece tarde e sem explicação.
 */

export const COVER_MEDIA_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const COVER_MAX_BYTES = 5 * 1024 * 1024;

export type CoverMediaType = (typeof COVER_MEDIA_TYPES)[number];
export type CoverRejection = "type" | "empty" | "too_large";

export type CoverChoice =
  | { ok: true; mediaType: CoverMediaType; byteSize: number }
  | { ok: false; reason: CoverRejection };

export function describeCoverFile(file: { type: string; size: number }): CoverChoice {
  const mediaType = COVER_MEDIA_TYPES.find((candidate) => candidate === file.type);
  if (!mediaType) return { ok: false, reason: "type" };
  if (!Number.isSafeInteger(file.size) || file.size < 1) return { ok: false, reason: "empty" };
  if (file.size > COVER_MAX_BYTES) return { ok: false, reason: "too_large" };
  return { ok: true, mediaType, byteSize: file.size };
}

export function coverRejectionMessage(reason: CoverRejection): string {
  if (reason === "type") return "A capa precisa ser JPEG, PNG ou WebP.";
  if (reason === "empty") return "Este arquivo está vazio. Escolha outro.";
  return "A capa precisa ter até 5 MB.";
}

/** O tamanho em MB, como o vendedor lê, para caber num aviso de recusa. */
export function coverSizeLabel(byteSize: number): string {
  return `${(byteSize / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`;
}

export function coverAcceptAttribute(): string {
  return COVER_MEDIA_TYPES.join(",");
}

/**
 * O digest que o intent declara e a assinatura prende.
 *
 * O S3 recusa o PUT cujo conteúdo não bata com este valor, então ele é o que
 * garante que o objeto na quarentena é o arquivo que o vendedor escolheu, e não
 * outro que tenha entrado no meio do caminho.
 */
export async function coverDigestHex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
