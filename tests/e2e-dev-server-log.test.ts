import { describe, expect, it } from "vitest";

import { compileLine } from "./e2e/support/dev-server-log.mjs";

describe("filtro do log do servidor do e2e (#585)", () => {
  it("deixa passar a compilação, sem o símbolo de estado", () => {
    expect(compileLine(" ○ Compiling /library ...")).toBe("Compiling /library ...");
    expect(compileLine(" ✓ Compiled /library in 2.3s")).toBe("Compiled /library in 2.3s");
    expect(compileLine("Compiled in 410ms")).toBe("Compiled in 410ms");
  });

  it("barra requisição, erro, env e qualquer outra linha", () => {
    for (const line of [
      " GET /auth/callback?code=abc&state=def 307 in 12ms",
      " POST /api/session 200 in 40ms",
      " ⨯ Error: falhou ao ler a sessão",
      " ⨯ Compiled with errors",
      "   - Environments: .env.local",
      "   - Local:        http://localhost:3000",
      " ✓ Ready in 1.2s",
      "[WebServer] {\"event\":\"oauth_callback_failed\"}",
      "Compilingx",
      "\u001b[32mCompiled\u001b[39m /x in 1s",
      ""
    ]) {
      expect(compileLine(line), line).toBeNull();
    }
  });

  it("não deixa a linha crescer sem limite", () => {
    const kept = compileLine(` ✓ Compiled /${"a".repeat(500)} in 1s`);
    expect(kept).not.toBeNull();
    expect(kept!.length).toBe(200);
  });
});
