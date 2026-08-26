import { describe, expect, it } from "vitest";

import { embedSource, isEmbedId, isEmbedProvider } from "../lib/lesson-embed";

/**
 * The embed is safe because the frame address is a literal template plus an
 * identifier that cannot contain a path or query separator. That safety is
 * structural, so these tests pin the structure: the two literal prefixes, and
 * the fact that nothing else can ever be produced.
 *
 * A future provider whose host comes from data, or an identifier pattern that
 * admits `/` or `?`, would reintroduce the risk quietly. It would fail here
 * first.
 */
describe("embedded video address", () => {
  it("builds only the two literal prefixes", () => {
    expect(embedSource("youtube", "dQw4w9WgXcQ"))
      .toBe("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ");
    expect(embedSource("vimeo", "76979871"))
      .toBe("https://player.vimeo.com/video/76979871");
  });

  it.each([
    "https://evil.example/x",
    "../../evil",
    "abc/../../etc",
    "abc?autoplay=1",
    "abc#fragment",
    "abc&x=1",
    "abc.evil",
    "abc%2F",
    "abc def",
    "<script>",
    "",
    "a".repeat(65)
  ])("refuses %s as an identifier", (candidate) => {
    expect(isEmbedId(candidate)).toBe(false);
    expect(embedSource("youtube", candidate)).toBeNull();
    expect(embedSource("vimeo", candidate)).toBeNull();
  });

  it.each(["tiktok", "dailymotion", "", "YOUTUBE", null, undefined, 1])(
    "refuses %s as a provider",
    (candidate) => {
      expect(isEmbedProvider(candidate)).toBe(false);
      expect(embedSource(candidate, "dQw4w9WgXcQ")).toBeNull();
    }
  );

  it("never produces an address outside the two known hosts", () => {
    // Whatever the identifier, the origin is one of two literals. This is the
    // property the iframe sandbox depends on.
    // Os identificadores agora seguem o formato de cada provedor: 11
    // caracteres no YouTube, dígitos no Vimeo. Antes qualquer coisa de 1 a 64
    // passava, e foi assim que um parâmetro de rastreamento virou "vídeo".
    const hosts = new Set<string>();
    const validos = {
      youtube: ["dQw4w9WgXcQ", "-vvGAKtV_Ek", "_-_-_-_-_-_"],
      vimeo: ["123456", "123456789012"]
    } as const;
    for (const provider of ["youtube", "vimeo"] as const) {
      for (const id of validos[provider]) {
        const source = embedSource(provider, id);
        expect(source, `${provider}/${id}`).not.toBeNull();
        hosts.add(new URL(source as string).origin);
      }
    }
    // E o que não tem o formato do provedor não vira endereço nenhum.
    expect(embedSource("youtube", "LW2Oweh9T56VW1nR")).toBeNull();
    expect(embedSource("vimeo", "dQw4w9WgXcQ")).toBeNull();
    expect([...hosts].sort()).toEqual([
      "https://player.vimeo.com",
      "https://www.youtube-nocookie.com"
    ]);
  });
});
