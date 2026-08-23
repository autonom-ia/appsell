import { describe, expect, it } from "vitest";

import { safePostLoginPath } from "../lib/post-login-redirect";

describe("post-login destination", () => {
  const origin = "https://sell.autonomia.site";

  it("keeps a same-origin product path", () => {
    expect(safePostLoginPath("/s/hub2you/p/curso/access", origin))
      .toBe("/s/hub2you/p/curso/access");
    expect(safePostLoginPath("/library?from=product", origin))
      .toBe("/library?from=product");
  });

  it("rejects open redirects and malformed destinations", () => {
    expect(safePostLoginPath("https://evil.test/account", origin)).toBeNull();
    expect(safePostLoginPath("//evil.test/account", origin)).toBeNull();
    expect(safePostLoginPath("javascript:alert(1)", origin)).toBeNull();
    expect(safePostLoginPath("/" + "a".repeat(512), origin)).toBeNull();
  });
});
