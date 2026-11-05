// Sobe o `next dev` do e2e e deixa passar para o log só as linhas de
// compilação ("Compiling", "Compiled", com a rota e a duração). O log do
// Actions é público: requisição, erro, env e dado de teste ficam de fora.
// Serve para casar uma trava de 30 s com o que o servidor fazia naquele
// segundo (#585). Sem regex: comparação por início de linha.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const PREFIXES = ["Compiling ", "Compiled "];
// O Next põe um símbolo de estado antes da mensagem (○ compilando, ✓ pronto).
const STATUS_GLYPHS = new Set(["○", "✓"]);
const MAX_LENGTH = 200;

/**
 * Devolve a linha de compilação sem o símbolo de estado, ou null quando a
 * linha não é de compilação e não pode ir para o log.
 * @param {string} line
 * @returns {string | null}
 */
export function compileLine(line) {
  const trimmed = line.trimStart();
  const firstSpace = trimmed.indexOf(" ");
  const head = firstSpace === -1 ? trimmed : trimmed.slice(0, firstSpace);
  const message = STATUS_GLYPHS.has(head) ? trimmed.slice(firstSpace + 1) : trimmed;
  if (!PREFIXES.some((prefix) => message.startsWith(prefix))) return null;
  return message.length > MAX_LENGTH ? message.slice(0, MAX_LENGTH) : message;
}

function run() {
  const child = spawn("pnpm", ["dev"], {
    stdio: ["ignore", "pipe", "inherit"],
    env: { ...process.env, NO_COLOR: "1", FORCE_COLOR: "0" }
  });
  createInterface({ input: child.stdout }).on("line", (line) => {
    const kept = compileLine(line);
    if (kept !== null) process.stdout.write(`[next] ${kept}\n`);
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => child.kill(signal));
  }
  child.on("exit", (code, signal) => {
    process.exit(code ?? (signal ? 1 : 0));
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) run();
