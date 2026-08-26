/**
 * The embedded-video contract, in one place.
 *
 * The identifier pattern and the provider list were duplicated across the BFF
 * route, the seller panel and the player. They were byte-identical, which is
 * exactly the arrangement that drifts without anyone noticing — and the safety
 * of the embed depends on them agreeing.
 *
 * The address is always assembled from a literal template plus an identifier
 * that cannot contain `/`, `.`, `:`, `?`, `#`, `&`, `=` or `%`. That is what
 * makes it impossible for a seller to steer the frame somewhere else, and it is
 * why the provider host must never come from data.
 */

export type EmbedProvider = "youtube" | "vimeo";

/**
 * O formato do identificador, por provedor.
 *
 * Era `[A-Za-z0-9_-]{1,64}` para os dois, e por isso aceitava quase tudo. Quem
 * cola `https://youtu.be/-vvGAKtV_Ek?si=LW2Oweh9T56VW1nR` e leva o pedaço
 * errado grava o `si=` — o parâmetro de rastreamento — e o vídeo nasce
 * quebrado, sem nenhum aviso. Espelha o que o banco passou a exigir em `0041`.
 */
export const EMBED_ID_PATTERNS: Readonly<Record<EmbedProvider, RegExp>> = {
  youtube: /^[A-Za-z0-9_-]{11}$/,
  vimeo: /^[0-9]{6,12}$/
};

/** Mantido para quem só precisa saber se a forma é plausível. */
export const EMBED_ID = /^(?:[A-Za-z0-9_-]{11}|[0-9]{6,12})$/;

const SOURCES: Readonly<Record<EmbedProvider, (id: string) => string>> = {
  youtube: (id) => `https://www.youtube-nocookie.com/embed/${id}`,
  vimeo: (id) => `https://player.vimeo.com/video/${id}`
};

export function isEmbedProvider(value: unknown): value is EmbedProvider {
  return value === "youtube" || value === "vimeo";
}

export function isEmbedId(value: unknown, provider?: unknown): value is string {
  if (typeof value !== "string") return false;
  if (isEmbedProvider(provider)) return EMBED_ID_PATTERNS[provider].test(value);
  return EMBED_ID.test(value);
}

/**
 * O identificador a partir do que o vendedor colou — link inteiro ou só o id.
 *
 * Pedir "o identificador, não o endereço inteiro" transfere para o vendedor um
 * trabalho que a máquina faz melhor, e o custo de errar é um vídeo morto na
 * aula de um aluno. Aceitar o link é o comportamento esperado; extrair aqui
 * mantém a montagem do endereço tão restrita quanto antes, porque o que sai
 * daqui ainda tem de passar por `EMBED_ID_PATTERNS`.
 */
export function embedIdFromInput(provider: unknown, raw: unknown): string | null {
  if (!isEmbedProvider(provider) || typeof raw !== "string") return null;
  const value = raw.trim();
  if (!value) return null;
  // Já é o identificador puro.
  if (EMBED_ID_PATTERNS[provider].test(value)) return value;

  let parsed: URL;
  try {
    parsed = new URL(value.includes("://") ? value : `https://${value}`);
  } catch {
    return null;
  }
  const host = parsed.hostname.replace(/^www[.]/, "").toLowerCase();
  const parts = parsed.pathname.split("/").filter(Boolean);

  if (provider === "youtube") {
    // youtu.be/<id> | youtube.com/watch?v=<id> | /embed/<id> | /shorts/<id> | /live/<id>
    const candidate = host === "youtu.be"
      ? parts[0]
      : host.endsWith("youtube.com") || host.endsWith("youtube-nocookie.com")
        ? (parsed.searchParams.get("v")
          ?? (["embed", "shorts", "live", "v"].includes(parts[0] ?? "") ? parts[1] : undefined))
        : undefined;
    return candidate && EMBED_ID_PATTERNS.youtube.test(candidate) ? candidate : null;
  }

  // vimeo.com/<digitos> | player.vimeo.com/video/<digitos>
  if (!host.endsWith("vimeo.com")) return null;
  const candidate = parts[0] === "video" ? parts[1] : parts[0];
  return candidate && EMBED_ID_PATTERNS.vimeo.test(candidate) ? candidate : null;
}

/** The frame address, or null when the row cannot be handed to a browser. */
export function embedSource(provider: unknown, videoId: unknown): string | null {
  // O provedor entra na checagem de propósito: sem ele, um identificador de
  // YouTube passava e era montado como endereço do Vimeo.
  if (!isEmbedProvider(provider) || !isEmbedId(videoId, provider)) return null;
  return SOURCES[provider](videoId);
}
