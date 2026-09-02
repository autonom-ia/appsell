import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { renderizarEmailTransacional } from "../lib/email/layout-transacional";

// A moldura existe aqui e em apps/api/src/appsell_api/email_layout.py, que é
// a que o worker usa. Os dois renderizam os fixtures de tests/fixtures/email/
// e têm de bater byte a byte com o golden gerado por ESTA moldura. Mudou a
// TS? Regenere o golden (scripts/email/gera-golden.mjs) e o teste Python
// acusa se a porta ficou para trás — e vice-versa.

const FIXTURES = join(__dirname, "fixtures", "email");

afterEach(() => {
  delete process.env.NEXT_PUBLIC_SITE_ORIGIN;
});

describe("paridade da moldura com o golden compartilhado", () => {
  for (const nome of ["compra-confirmada", "minimo"]) {
    it(`renderiza ${nome} igual ao golden`, () => {
      const { origem, email } = JSON.parse(readFileSync(join(FIXTURES, `${nome}.json`), "utf8"));
      process.env.NEXT_PUBLIC_SITE_ORIGIN = origem;
      const { html, text } = renderizarEmailTransacional(email);
      expect(html).toBe(readFileSync(join(FIXTURES, `${nome}.html`), "utf8"));
      expect(text).toBe(readFileSync(join(FIXTURES, `${nome}.txt`), "utf8"));
    });
  }
});
