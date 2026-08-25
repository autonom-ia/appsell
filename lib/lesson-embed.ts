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

export const EMBED_ID = /^[A-Za-z0-9_-]{1,64}$/;

export type EmbedProvider = "youtube" | "vimeo";

const SOURCES: Readonly<Record<EmbedProvider, (id: string) => string>> = {
  youtube: (id) => `https://www.youtube-nocookie.com/embed/${id}`,
  vimeo: (id) => `https://player.vimeo.com/video/${id}`
};

export function isEmbedProvider(value: unknown): value is EmbedProvider {
  return value === "youtube" || value === "vimeo";
}

export function isEmbedId(value: unknown): value is string {
  return typeof value === "string" && EMBED_ID.test(value);
}

/** The frame address, or null when the row cannot be handed to a browser. */
export function embedSource(provider: unknown, videoId: unknown): string | null {
  if (!isEmbedProvider(provider) || !isEmbedId(videoId)) return null;
  return SOURCES[provider](videoId);
}
