import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

// A cabeça da cadeia do Alembic, achada pelo `down_revision`, e não pelo nome.
//
// O número da migration é reservado por trilha, e o `down_revision` é o head da
// main na hora do merge: a 0068 entrou depois da 0073. Ordenar os arquivos pelo
// nome chamaria de cabeça uma migration que não é.

type Revisoes = { readonly revision: string; readonly downRevision: string | null };

/** Valor de `nome = "..."` (ou `nome: tipo = "..."`) no topo do arquivo. */
function atribuido(fonte: string, nome: string, arquivo: string): string | null | undefined {
  for (const linha of fonte.split("\n")) {
    if (linha.startsWith(" ") || linha.startsWith("\t")) continue;
    const igual = linha.indexOf("=");
    if (igual < 0 || linha.slice(0, igual).split(":")[0].trim() !== nome) continue;
    const valor = linha.slice(igual + 1).trim();
    if (valor === "None") return null;
    if (valor.length >= 2 && valor.startsWith('"') && valor.endsWith('"')) return valor.slice(1, -1);
    throw new Error(`${arquivo}: ${nome} não é texto literal`);
  }
  return undefined;
}

function revisoes(pasta: string, arquivo: string): Revisoes {
  const fonte = readFileSync(resolve(pasta, arquivo), "utf8");
  const revision = atribuido(fonte, "revision", arquivo);
  if (typeof revision !== "string") throw new Error(`${arquivo}: sem revision`);
  return { revision, downRevision: atribuido(fonte, "down_revision", arquivo) ?? null };
}

/** A revision da cabeça. Falha se a cadeia bifurca ou deixa arquivo de fora. */
export function migrationHead(root: string): string {
  const pasta = resolve(root, "apps/api/migrations/versions");
  const arquivos = readdirSync(pasta).filter(
    (nome) => nome.endsWith(".py") && nome.length > 5 && Number.isInteger(Number(nome.slice(0, 4))) && nome[4] === "_"
  );
  const porPai = new Map<string | null, string>();
  for (const arquivo of arquivos) {
    const { revision, downRevision } = revisoes(pasta, arquivo);
    if (porPai.has(downRevision)) throw new Error(`duas migrations partem de ${downRevision}`);
    porPai.set(downRevision, revision);
  }
  let cabeca: string | null = null;
  let elos = 0;
  while (porPai.has(cabeca)) {
    cabeca = porPai.get(cabeca)!;
    elos += 1;
  }
  if (cabeca === null || elos !== arquivos.length) {
    throw new Error(`a cadeia tem ${elos} elos e o diretório tem ${arquivos.length} migrations`);
  }
  return cabeca;
}
