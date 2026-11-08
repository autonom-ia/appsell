import { ehCabecalhoSensivel, REDIGIDO, redigirTexto } from "./redacao";

// O Next imprime o erro cru no mesmo stdout/stderr (#549): o `logError` do
// base-server e o tratador de erro de render escrevem `⨯ Error: <mensagem>`
// com o stack, sem passar pelo `onRequestError`. Medido com `next start`: o
// e-mail e o CPF da mensagem saíam inteiros.
//
// `instrumentation.register` envolve o console do servidor com este redator.
// Uma linha JSON (a do `onRequestError`, as de telemetria) continua JSON: os
// valores são redigidos e a linha é montada de novo. O resto sai em texto,
// redigido, e não muda a contagem do alerta do #472, que só lê JSON.

const METODOS = ["error", "warn", "log", "info", "debug"] as const;
const JA_INSTALADO = Symbol.for("appsell.console-redigido");
const PROFUNDIDADE_MAXIMA = 8;
const PROFUNDO_DEMAIS = "<too-deep>";
const CAUSAS_MAXIMAS = 3;

type Metodo = (...args: unknown[]) => void;
type ConsoleDoServidor = Record<(typeof METODOS)[number], Metodo> & { [JA_INSTALADO]?: true };

export function instalarConsoleRedigido(alvo: ConsoleDoServidor = console as unknown as ConsoleDoServidor): void {
  if (alvo[JA_INSTALADO]) return;
  for (const metodo of METODOS) {
    const original = alvo[metodo].bind(alvo);
    alvo[metodo] = (...args: unknown[]) => original(linhaRedigida(args));
  }
  alvo[JA_INSTALADO] = true;
}

export function linhaRedigida(args: readonly unknown[]): string {
  const [unico] = args;
  if (args.length === 1 && typeof unico === "string") {
    const json = linhaJsonRedigida(unico);
    if (json !== null) return json;
  }
  return redigirTexto(args.map(comoTexto).join(" "));
}

function linhaJsonRedigida(texto: string): string | null {
  const aparado = texto.trim();
  if (!aparado.startsWith("{") || !aparado.endsWith("}")) return null;
  let valor: unknown;
  try {
    valor = JSON.parse(aparado);
  } catch {
    // Começa e termina com chave, mas não é JSON: sai como texto redigido.
    return null;
  }
  return JSON.stringify(valorRedigido(valor, 0));
}

function valorRedigido(valor: unknown, profundidade: number): unknown {
  if (typeof valor === "string") return redigirTexto(valor);
  if (typeof valor === "number" || typeof valor === "bigint") {
    // `documento: 52998224725` chega como número e sairia inteiro.
    const texto = String(valor);
    return redigirTexto(texto) === texto ? (typeof valor === "bigint" ? texto : valor) : REDIGIDO;
  }
  if (valor === null || typeof valor !== "object") return valor;
  if (profundidade >= PROFUNDIDADE_MAXIMA) return PROFUNDO_DEMAIS;
  if (valor instanceof Error) return redigirTexto(textoDoErro(valor, 0));
  if (Array.isArray(valor)) return valor.map((item) => valorRedigido(item, profundidade + 1));
  return Object.fromEntries(Object.entries(valor).map(([chave, item]) => [
    chave,
    ehCabecalhoSensivel(chave) && item !== null && item !== undefined ? REDIGIDO : valorRedigido(item, profundidade + 1)
  ]));
}

function comoTexto(valor: unknown): string {
  if (typeof valor === "string") return valor;
  if (valor instanceof Error) return textoDoErro(valor, 0);
  if (valor === null || typeof valor !== "object") return String(valor);
  return JSON.stringify(valorRedigido(valor, 0)) ?? String(valor);
}

function textoDoErro(erro: Error, causas: number): string {
  const base = erro.stack ?? `${erro.name}: ${erro.message}`;
  const digest = (erro as { digest?: unknown }).digest;
  const comDigest = typeof digest === "string" ? `${base} { digest: '${digest}' }` : base;
  const causa = (erro as { cause?: unknown }).cause;
  if (causa instanceof Error && causas < CAUSAS_MAXIMAS) {
    return `${comDigest}\n[cause]: ${textoDoErro(causa, causas + 1)}`;
  }
  return comDigest;
}
