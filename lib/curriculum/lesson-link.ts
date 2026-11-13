import { embedIdFromInput, type EmbedProvider } from "@/lib/lesson-embed";
import { safeLessonLink } from "@/lib/lesson-markup";

/**
 * O link colado num campo só, e o que ele vira (#649).
 *
 * Antes eram dois `<select>` (tipo e provedor) e um campo "Endereço (https)".
 * Quem vende sabe que tem um link; saber se ele é "vídeo incorporado" de qual
 * provedor é trabalho da máquina. O endereço é lido com `URL`, e o
 * identificador do vídeo sai de `embedIdFromInput`, a mesma regra que a rota e
 * o player usam.
 */

export type LessonLink =
  | { kind: "embed"; provider: EmbedProvider; videoId: string }
  | { kind: "external"; url: string; host: string };

const PROVIDERS: readonly EmbedProvider[] = ["youtube", "vimeo"];

export function classifyLessonLink(raw: string): LessonLink | null {
  const value = raw.trim();
  if (!value || value.includes(" ")) return null;
  const withScheme = value.includes("://") ? value : `https://${value}`;
  const safe = safeLessonLink(withScheme);
  if (!safe) return null;
  const parsed = new URL(safe);
  if (!parsed.hostname.includes(".")) return null;
  for (const provider of PROVIDERS) {
    const videoId = embedIdFromInput(provider, safe);
    if (videoId && hostBelongsTo(parsed.hostname, provider)) {
      return { kind: "embed", provider, videoId };
    }
  }
  return { kind: "external", url: safe, host: displayHost(parsed.hostname) };
}

export function suggestedLinkTitle(link: LessonLink): string {
  if (link.kind === "embed") return link.provider === "youtube" ? "Vídeo do YouTube" : "Vídeo do Vimeo";
  return link.host;
}

function hostBelongsTo(hostname: string, provider: EmbedProvider): boolean {
  const host = displayHost(hostname);
  if (provider === "youtube") return host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com");
  return host === "vimeo.com" || host.endsWith(".vimeo.com");
}

function displayHost(hostname: string): string {
  const host = hostname.toLowerCase();
  return host.startsWith("www.") ? host.slice(4) : host;
}
