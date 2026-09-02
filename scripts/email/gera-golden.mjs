// Regenera os goldens de tests/fixtures/email a partir da moldura TS, que é a
// fonte. Rode depois de mudar lib/email/layout-transacional.ts; a porta Python
// (apps/api/src/appsell_api/email_layout.py) tem de acompanhar, e o pytest
// acusa se não acompanhar.
//
//   node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON scripts/email/gera-golden.mjs

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { renderizarEmailTransacional } from "../../lib/email/layout-transacional.ts";

const pasta = join(process.cwd(), "tests", "fixtures", "email");
for (const arquivo of readdirSync(pasta).filter((nome) => nome.endsWith(".json"))) {
  const nome = arquivo.slice(0, -".json".length);
  const { origem, email } = JSON.parse(readFileSync(join(pasta, arquivo), "utf8"));
  process.env.NEXT_PUBLIC_SITE_ORIGIN = origem;
  const { html, text } = renderizarEmailTransacional(email);
  writeFileSync(join(pasta, `${nome}.html`), html);
  writeFileSync(join(pasta, `${nome}.txt`), text);
  console.log(`${nome}: html ${html.length} bytes, texto ${text.length} bytes`);
}
