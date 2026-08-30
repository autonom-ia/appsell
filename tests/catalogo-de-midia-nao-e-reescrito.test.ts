import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * O par PDF/MP4 não pode voltar a ser escrito à mão em lugar nenhum.
 *
 * Ele já foi corrigido duas vezes, e sobreviveu as duas:
 *
 * - **29/08/2026, #291** — a migration `0042` tirou do banco
 *   `p_ready_media not in ('application/pdf','video/mp4')`, que impedia
 *   documento de ser registrado. Escrevi um teste para impedir o retorno:
 *   `test_nenhuma_funcao_fecha_o_conjunto_de_midia_em_pdf_e_mp4`, que varre
 *   `pg_proc`.
 * - **30/08/2026, #308** — o aluno não abriu a aula. A mesma lista estava no
 *   validador do player, em TypeScript. A varredura de `pg_proc` não a
 *   alcançava. Uma aula com um único anexo `.md` não abria — nem os anexos,
 *   nem o PDF, nem a aula, porque um item fora da lista invalidava o payload
 *   inteiro.
 *
 * A lição é que a varredura precisa cobrir as três linguagens onde o catálogo
 * vive: SQL, Python e TypeScript. Corrigir a classe num lugar e testar só
 * naquele lugar foi exatamente o que deixou a segunda cópia passar.
 *
 * O SQL continua coberto pelo teste em `pg_proc`, que alcança o que já está
 * gravado no banco. Este arquivo cobre o código-fonte das três.
 */

const RAIZ = fileURLToPath(new URL("..", import.meta.url));
const PASTAS = ["app", "lib", "apps/api/src", "apps/api/migrations/versions"];
const EXTENSOES = [".ts", ".tsx", ".py", ".sql"];

/**
 * Onde o par é legítimo, e por quê. Qualquer outro lugar é uma cópia.
 */
const PERMITIDOS = new Map([
  // A fonte única do navegador: é aqui que o par nasce.
  ["lib/asset-media.ts", "o catálogo do navegador, de onde todos derivam"],
  // A fonte única do servidor.
  ["apps/api/src/appsell_api/asset_media.py", "o catálogo da API"],
  // Migrations são histórico: reescrevê-las mudaria o passado, e o estado atual
  // do banco é verificado pelo teste que varre `pg_proc`.
  ["apps/api/migrations/", "histórico; o estado atual é verificado em pg_proc"]
]);

function arquivos(pasta: string): string[] {
  const alvo = join(RAIZ, pasta);
  let entradas: string[];
  try {
    entradas = readdirSync(alvo);
  } catch {
    return [];
  }
  return entradas.flatMap((nome) => {
    const caminho = join(alvo, nome);
    if (statSync(caminho).isDirectory()) return arquivos(join(pasta, nome));
    return EXTENSOES.some((ext) => nome.endsWith(ext)) ? [join(pasta, nome)] : [];
  });
}

function permitido(caminho: string): string | null {
  for (const [prefixo, motivo] of PERMITIDOS) {
    if (caminho === prefixo || caminho.startsWith(prefixo)) return motivo;
  }
  return null;
}

describe("o catálogo de mídia tem uma fonte por linguagem", () => {
  it("ninguém reescreve o par PDF/MP4 fora do catálogo", () => {
    const culpados: string[] = [];
    for (const pasta of PASTAS) {
      for (const caminho of arquivos(pasta)) {
        if (permitido(caminho)) continue;
        const linhas = readFileSync(join(RAIZ, caminho), "utf-8").split("\n");
        linhas.forEach((linha, indice) => {
          // Os dois literais na mesma linha: é assim que a lista fechada se
          // escreve, em qualquer das três linguagens.
          if (linha.includes("application/pdf") && linha.includes("video/mp4")) {
            culpados.push(`${relative(".", caminho)}:${indice + 1}  ${linha.trim().slice(0, 100)}`);
          }
        });
      }
    }
    expect(culpados, [
      "O par PDF/MP4 foi reescrito fora do catálogo.",
      "Derive de `lib/asset-media.ts` (TS) ou `asset_media.py` (Python).",
      "Já custou duas entregas: #291 (documento não registrava) e #308 (aluno",
      "não abria a aula). Se o lugar for legítimo, acrescente-o a PERMITIDOS",
      "com o motivo.",
      ...culpados
    ].join("\n")).toEqual([]);
  });

  it("a varredura enxerga o que deveria enxergar", () => {
    // Sem isto, um erro no caminho ou no filtro faria o teste passar por não
    // ler arquivo nenhum — o pior desfecho possível para uma varredura.
    const lidos = PASTAS.flatMap(arquivos);
    expect(lidos.length).toBeGreaterThan(250);
    expect(lidos.some((c) => c.endsWith(".py"))).toBe(true);
    expect(lidos.some((c) => c.endsWith(".ts"))).toBe(true);
    expect(lidos.some((c) => c.endsWith(".tsx"))).toBe(true);
    expect(lidos).toContain("lib/asset-media.ts");
  });
});
