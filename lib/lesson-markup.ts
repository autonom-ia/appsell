/**
 * A tiny markup subset for lesson descriptions, parsed into data.
 *
 * The parser never produces a string of HTML, and nothing downstream is allowed
 * to. The player turns these nodes into React elements, so the absence of
 * injection is a property of the shape of the output rather than a promise made
 * by a sanitiser: there is no code path that can carry markup from a seller into
 * the browser's parser, because no step ever hands the browser markup at all.
 *
 * What a seller can write:
 *
 *   **bold**   *italic*   `code`
 *   - a bullet
 *   [rótulo](https://exemplo.com)
 *
 * Everything else is literal text, including every character that would matter
 * to an HTML or a URL parser. A link is accepted only over https; anything else
 * stays as the literal characters the seller typed.
 */

const MAX_INPUT_LENGTH = 8_192;
const MAX_BLOCKS = 200;
const MAX_LINK_LENGTH = 2_000;

export type InlineNode =
  | { readonly type: "text"; readonly value: string }
  | { readonly type: "strong"; readonly value: string }
  | { readonly type: "emphasis"; readonly value: string }
  | { readonly type: "code"; readonly value: string }
  | { readonly type: "link"; readonly href: string; readonly label: string };

export type BlockNode =
  | { readonly type: "paragraph"; readonly children: readonly InlineNode[] }
  | { readonly type: "list"; readonly items: readonly (readonly InlineNode[])[] };

/**
 * An https URL with nothing in it that a URL or an attribute parser would read
 * as a boundary. `new URL` settles the scheme rather than a regular expression,
 * because a regular expression is what people get wrong about URLs.
 */
export function safeLessonLink(candidate: string): string | null {
  if (!candidate || candidate.length > MAX_LINK_LENGTH) return null;
  if (/[\s<>"'`\\]/.test(candidate)) return null;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  if (!parsed.hostname) return null;
  // `https://suporte.exemplo.com@evil.example/` is a real address at
  // evil.example, and the part a reader's eye lands on first is the part that
  // is not the host. No lesson link needs credentials, so the whole userinfo
  // component is refused rather than displayed carefully.
  if (parsed.username || parsed.password) return null;
  return parsed.toString();
}

export function parseLessonMarkup(source: string): readonly BlockNode[] {
  if (!source) return [];
  const normalised = source.slice(0, MAX_INPUT_LENGTH).replace(/\r\n?/g, "\n");
  const blocks: BlockNode[] = [];
  for (const chunk of normalised.split(/\n{2,}/)) {
    if (blocks.length >= MAX_BLOCKS) break;
    const lines = chunk.split("\n").map((line) => line.trim()).filter(Boolean);
    if (lines.length === 0) continue;
    if (lines.every(isBullet)) {
      blocks.push({
        type: "list",
        items: lines.map((line) => parseInline(line.slice(2).trim()))
      });
      continue;
    }
    blocks.push({ type: "paragraph", children: parseInline(lines.join(" ")) });
  }
  return blocks;
}

function isBullet(line: string) {
  return line.startsWith("- ") && line.length > 2;
}

/**
 * One left-to-right pass. Each pattern must close within the same block or it
 * stays as the literal characters that were typed, so an unbalanced marker can
 * never swallow the rest of the description.
 */
function parseInline(source: string): readonly InlineNode[] {
  const nodes: InlineNode[] = [];
  let pending = "";
  let index = 0;

  const flush = () => {
    if (pending) {
      nodes.push({ type: "text", value: pending });
      pending = "";
    }
  };

  while (index < source.length) {
    const rest = source.slice(index);

    const link = matchLink(rest);
    if (link) {
      flush();
      nodes.push(link.node);
      index += link.length;
      continue;
    }

    const strong = matchDelimited(rest, "**");
    if (strong) {
      flush();
      nodes.push({ type: "strong", value: strong.value });
      index += strong.length;
      continue;
    }

    const emphasis = matchDelimited(rest, "*");
    if (emphasis) {
      flush();
      nodes.push({ type: "emphasis", value: emphasis.value });
      index += emphasis.length;
      continue;
    }

    const code = matchDelimited(rest, "`");
    if (code) {
      flush();
      nodes.push({ type: "code", value: code.value });
      index += code.length;
      continue;
    }

    pending += source[index];
    index += 1;
  }

  flush();
  return nodes;
}

function matchDelimited(rest: string, marker: string) {
  if (!rest.startsWith(marker)) return null;
  const closing = rest.indexOf(marker, marker.length);
  if (closing < 0) return null;
  const value = rest.slice(marker.length, closing);
  if (!value.trim() || value.includes("\n")) return null;
  return { value, length: closing + marker.length };
}

/** `[label](https://host/path)` — the label is always literal text. */
function matchLink(rest: string) {
  if (!rest.startsWith("[")) return null;
  const labelEnd = rest.indexOf("]");
  if (labelEnd < 1 || rest[labelEnd + 1] !== "(") return null;
  const hrefEnd = rest.indexOf(")", labelEnd + 2);
  if (hrefEnd < 0) return null;
  const label = rest.slice(1, labelEnd);
  const href = safeLessonLink(rest.slice(labelEnd + 2, hrefEnd));
  if (!label.trim() || !href) return null;
  return {
    node: { type: "link", href, label } as const,
    length: hrefEnd + 1
  };
}
