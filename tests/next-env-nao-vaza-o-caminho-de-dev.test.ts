import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * `next-env.d.ts` é gerado, e o dev server o reescreve.
 *
 * Rodar `pnpm test:e2e` levanta o Next em modo de desenvolvimento, que troca a
 * linha de `./.next/types/routes.d.ts` por `./.next/dev/types/routes.d.ts`. A
 * pasta `.next` é ignorada pelo git, então em checkout limpo esse arquivo não
 * existe e `pnpm typecheck` quebra — antes mesmo do build gerar qualquer coisa.
 *
 * Isso já entrou em duas PRs sem relação nenhuma com o assunto delas, as duas
 * vezes pego no review e não pelo CI. O ciclo é sempre o mesmo: rodo o e2e,
 * commito, e a alteração viaja junto. Uma guarda no CI custa dez linhas e
 * encerra a recorrência.
 */

const CAMINHO = fileURLToPath(new URL("../next-env.d.ts", import.meta.url));

describe("next-env.d.ts fica no caminho de build", () => {
  it("não aponta para a pasta de desenvolvimento", () => {
    const conteudo = readFileSync(CAMINHO, "utf-8");
    // Esta asserção vem primeiro de propósito: é a que carrega a instrução de
    // restauro, e uma asserção que falha antes impediria a mensagem de aparecer.
    expect(
      conteudo.includes(".next/dev/"),
      [
        "`next-env.d.ts` está apontando para `.next/dev/`.",
        "O dev server reescreveu o arquivo — provavelmente ao rodar `pnpm test:e2e`.",
        "Restaure antes de commitar:",
        "  git checkout origin/main -- next-env.d.ts"
      ].join("\n")
    ).toBe(false);
    expect(conteudo).toContain('import "./.next/types/routes.d.ts";');
  });
});
