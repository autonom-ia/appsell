import { describe, expect, it } from "vitest";

import {
  parseLessonMarkup,
  safeLessonLink,
  type BlockNode,
  type InlineNode
} from "../lib/lesson-markup";

/**
 * A2.8 evidence.
 *
 * The claim under test is not "the parser escapes these payloads" — it is that
 * the parser has no way to emit markup at all. So each payload is checked twice:
 * every inline node it produces must be plain text carrying the payload
 * verbatim, and no node may be a link. A payload that survived as an anchor, or
 * as anything other than text, would be a finding even if it looked inert.
 */
const XSS_PAYLOADS = [
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "<svg/onload=alert(1)>",
  "<iframe src=javascript:alert(1)></iframe>",
  "<body onload=alert(1)>",
  "\"><script>alert(document.cookie)</script>",
  "'><img src=x onerror=alert(1)>",
  "<a href=\"javascript:alert(1)\">clique</a>",
  "<div style=\"background:url(javascript:alert(1))\">x</div>",
  "<object data=\"data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==\"></object>",
  "<meta http-equiv=\"refresh\" content=\"0;url=javascript:alert(1)\">",
  "<form action=\"https://evil.example\"><input name=x></form>",
  "<style>@import 'https://evil.example/x.css';</style>",
  "<base href=\"https://evil.example/\">",
  "javascript:alert(1)",
  "data:text/html,<script>alert(1)</script>",
  "vbscript:msgbox(1)",
  "&lt;script&gt;alert(1)&lt;/script&gt;",
  "&#60;script&#62;alert(1)&#60;/script&#62;",
  "<script>/*</script><script>*/alert(1)</script>",
  "<!--<script>alert(1)</script>-->",
  "<textarea></textarea><script>alert(1)</script>",
  "<noscript><p title=\"</noscript><script>alert(1)</script>\">",
  "<math><mtext><script>alert(1)</script></mtext></math>",
  "<template><script>alert(1)</script></template>"
] as const;

function inlineNodes(blocks: readonly BlockNode[]): readonly InlineNode[] {
  return blocks.flatMap((block) =>
    block.type === "paragraph" ? [...block.children] : block.items.flatMap((item) => [...item])
  );
}

function renderedText(blocks: readonly BlockNode[]): string {
  return inlineNodes(blocks)
    .map((node) => (node.type === "link" ? node.label : node.value))
    .join("");
}

/**
 * Every node type other than `link` becomes a React element with its value as a
 * text child, which the renderer cannot turn into markup. `link` is the only
 * node that reaches an attribute, so it is the only one a payload must never
 * produce.
 */
const INERT_TYPES = new Set(["text", "strong", "emphasis", "code"]);

describe("A2.8 — lesson description cannot carry a payload into the player", () => {
  it.each(XSS_PAYLOADS)("turns %s into inert nodes and never into a link", (payload) => {
    // Arrange / Act
    const blocks = parseLessonMarkup(payload);
    const nodes = inlineNodes(blocks);

    // Assert
    expect(nodes.length).toBeGreaterThan(0);
    expect(nodes.every((node) => INERT_TYPES.has(node.type))).toBe(true);
    expect(nodes.some((node) => node.type === "link")).toBe(false);
  });

  it.each(XSS_PAYLOADS.filter((payload) => !/[*`]/.test(payload)))(
    "keeps %s verbatim in the visible text",
    (payload) => {
      // A payload containing * or ` has those characters read as emphasis or
      // code markers, so its visible text loses them. That is inert either way,
      // and the payloads without those characters prove nothing else is dropped.
      expect(renderedText(parseLessonMarkup(payload))).toContain(payload.trim());
    }
  );

  it("reads the asterisks inside a comment-splitting payload as emphasis, not as markup", () => {
    const blocks = parseLessonMarkup("<script>/*</script><script>*/alert(1)</script>");
    const nodes = inlineNodes(blocks);

    expect(nodes.some((node) => node.type === "emphasis")).toBe(true);
    expect(nodes.every((node) => INERT_TYPES.has(node.type))).toBe(true);
    expect(renderedText(blocks)).toContain("alert(1)");
  });

  it("refuses every non-https scheme inside link markup", () => {
    const schemes = [
      "javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "vbscript:msgbox(1)",
      "http://exemplo.com",
      "file:///etc/passwd",
      "JaVaScRiPt:alert(1)",
      " javascript:alert(1)"
    ];

    for (const scheme of schemes) {
      const blocks = parseLessonMarkup(`[clique](${scheme})`);
      expect(inlineNodes(blocks).some((node) => node.type === "link")).toBe(false);
    }
  });

  it("refuses a link whose href hides a quote, a bracket or whitespace", () => {
    expect(safeLessonLink("https://exemplo.com/\" onmouseover=\"alert(1)")).toBeNull();
    expect(safeLessonLink("https://exemplo.com/<script>")).toBeNull();
    expect(safeLessonLink("https://exemplo.com/ x")).toBeNull();
    expect(safeLessonLink("https://exemplo.com/'")).toBeNull();
    expect(safeLessonLink("https://exemplo.com/`")).toBeNull();
    expect(safeLessonLink("https://exemplo.com/\\")).toBeNull();
  });

  it("refuses a link longer than the bounded length", () => {
    expect(safeLessonLink(`https://exemplo.com/${"a".repeat(2_000)}`)).toBeNull();
  });

  it("refuses userinfo, which makes the host read as something it is not", () => {
    // https://suporte.exemplo.com@evil.example/ is an address at evil.example,
    // and the part the eye lands on first is the part that is not the host.
    expect(safeLessonLink("https://suporte.exemplo.com@evil.example/login")).toBeNull();
    expect(safeLessonLink("https://user:senha@evil.example/")).toBeNull();
    expect(safeLessonLink("https://exemplo.com@evil.example/")).toBeNull();

    const blocks = parseLessonMarkup("[Suporte oficial](https://exemplo.com@evil.example/login)");
    expect(inlineNodes(blocks).some((node) => node.type === "link")).toBe(false);
  });
});

describe("lesson markup renders the subset a seller is offered", () => {
  it("splits blank-line separated paragraphs", () => {
    const blocks = parseLessonMarkup("Primeiro parágrafo.\n\nSegundo parágrafo.");

    expect(blocks).toHaveLength(2);
    expect(blocks[0]?.type).toBe("paragraph");
    expect(renderedText([blocks[0] as BlockNode])).toBe("Primeiro parágrafo.");
  });

  it("reads bold, italic and code", () => {
    const blocks = parseLessonMarkup("Isto é **forte**, isto é *ênfase* e isto é `código`.");
    const kinds = inlineNodes(blocks).map((node) => node.type);

    expect(kinds).toContain("strong");
    expect(kinds).toContain("emphasis");
    expect(kinds).toContain("code");
  });

  it("reads a bullet list", () => {
    const blocks = parseLessonMarkup("- primeiro\n- segundo");

    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.type).toBe("list");
    expect(blocks[0]?.type === "list" ? blocks[0].items : []).toHaveLength(2);
  });

  it("accepts an https link and keeps its label as text", () => {
    const blocks = parseLessonMarkup("Veja o [material](https://exemplo.com/guia.pdf).");
    const link = inlineNodes(blocks).find((node) => node.type === "link");

    expect(link).toBeDefined();
    expect(link?.type === "link" ? link.href : "").toBe("https://exemplo.com/guia.pdf");
    expect(link?.type === "link" ? link.label : "").toBe("material");
  });

  it("leaves an unbalanced marker as the characters the seller typed", () => {
    const blocks = parseLessonMarkup("um **asterisco solto e o resto da frase");

    expect(renderedText(blocks)).toBe("um **asterisco solto e o resto da frase");
    expect(inlineNodes(blocks).every((node) => node.type === "text")).toBe(true);
  });

  it("returns nothing for an empty description", () => {
    expect(parseLessonMarkup("")).toHaveLength(0);
    expect(parseLessonMarkup("   \n\n  ")).toHaveLength(0);
  });
});
