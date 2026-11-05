// Política de conteúdo em modo só-relatório (#548). O navegador não bloqueia
// nada: só avisa, em /api/csp-report, o que a política teria barrado. Depois de
// uma semana de relatos lidos, a política é ajustada e o modo bloqueante vem
// em PR separado.
//
// A política começa apertada de propósito: o que estiver faltando nela aparece
// nos relatos (o storage, o checkout do Financial, scripts de terceiros), e é
// isso que a semana de leitura precisa ver.

export const CSP_REPORT_PATH = "/api/csp-report";
export const CSP_REPORT_GROUP = "csp";

const DIRETIVAS: ReadonlyArray<readonly [string, string]> = [
  ["default-src", "'self'"],
  ["script-src", "'self' 'unsafe-inline'"],
  ["style-src", "'self' 'unsafe-inline'"],
  ["img-src", "'self' data: blob:"],
  ["font-src", "'self' data:"],
  ["connect-src", "'self'"],
  ["media-src", "'self' blob:"],
  ["frame-src", "'self'"],
  ["frame-ancestors", "'none'"],
  ["form-action", "'self'"],
  ["base-uri", "'self'"],
  ["object-src", "'none'"],
  ["report-uri", CSP_REPORT_PATH],
  ["report-to", CSP_REPORT_GROUP]
];

export function cspReportOnlyPolicy(): string {
  return DIRETIVAS.map(([nome, valor]) => `${nome} ${valor}`).join("; ");
}

export function cspReportOnlyHeaders(): ReadonlyArray<{ key: string; value: string }> {
  return [
    { key: "Content-Security-Policy-Report-Only", value: cspReportOnlyPolicy() },
    { key: "Reporting-Endpoints", value: `${CSP_REPORT_GROUP}="${CSP_REPORT_PATH}"` }
  ];
}

// O endpoint é público. Cada relato é pequeno; o que passar disso é abuso.
export const MAX_REPORT_BYTES = 8 * 1024;
const MAX_CAMPO = 200;
// Regra do Rodrigo: sequência de 32+ caracteres [A-Za-z0-9_-] é tratada como
// credencial. Feito com método de string, sem regex.
const TAMANHO_DE_CREDENCIAL = 32;
const CARACTERES_DE_CREDENCIAL = new Set(
  "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
);
// Valores que o navegador manda no lugar de uma URL em `blocked-uri`.
const PALAVRAS_DO_NAVEGADOR = new Set(["inline", "eval", "data", "blob", "self", "wasm-eval"]);

export type CspViolation = {
  readonly directive: string;
  readonly blocked: string;
  readonly document_path: string;
  readonly source: string;
  readonly line: number | null;
  readonly column: number | null;
  readonly disposition: string;
};

// Aceita os dois formatos que os navegadores mandam: o antigo
// `{"csp-report": {...}}` (report-uri) e a lista da Reporting API
// (`[{type: "csp-violation", body: {...}}]`). Devolve só os campos que
// importam para ajustar a política, já sem query string, fragmento e
// qualquer valor com cara de credencial. `script-sample` nunca sai daqui:
// ele copia trecho da página.
export function parseCspReports(corpo: unknown): CspViolation[] {
  if (Array.isArray(corpo)) {
    return corpo
      .filter((item) => objeto(item) && item.type === "csp-violation" && objeto(item.body))
      .map((item) => violacao((item as { body: Record<string, unknown> }).body, moderno));
  }
  if (objeto(corpo) && objeto(corpo["csp-report"])) {
    return [violacao(corpo["csp-report"] as Record<string, unknown>, antigo)];
  }
  return [];
}

type Campos = {
  readonly directive: readonly string[];
  readonly blocked: string;
  readonly document: string;
  readonly source: string;
  readonly line: string;
  readonly column: string;
};

const antigo: Campos = {
  directive: ["effective-directive", "violated-directive"],
  blocked: "blocked-uri",
  document: "document-uri",
  source: "source-file",
  line: "line-number",
  column: "column-number"
};

const moderno: Campos = {
  directive: ["effectiveDirective"],
  blocked: "blockedURL",
  document: "documentURL",
  source: "sourceFile",
  line: "lineNumber",
  column: "columnNumber"
};

function violacao(bruto: Record<string, unknown>, campos: Campos): CspViolation {
  const diretiva = campos.directive.map((nome) => bruto[nome]).find((valor) => typeof valor === "string");
  return {
    directive: limpo(texto(diretiva).split(" ")[0] ?? ""),
    blocked: semConsulta(texto(bruto[campos.blocked])),
    document_path: caminho(texto(bruto[campos.document])),
    source: semConsulta(texto(bruto[campos.source])),
    line: numero(bruto[campos.line]),
    column: numero(bruto[campos.column]),
    disposition: limpo(texto(bruto.disposition))
  };
}

function objeto(valor: unknown): valor is Record<string, unknown> {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

function texto(valor: unknown): string {
  return typeof valor === "string" ? valor : "";
}

function numero(valor: unknown): number | null {
  return typeof valor === "number" && Number.isInteger(valor) && valor >= 0 ? valor : null;
}

// Origem e caminho, sem query string nem fragmento: a query de uma URL
// assinada ou de um link de acesso carrega o próprio segredo.
function semConsulta(valor: string): string {
  if (PALAVRAS_DO_NAVEGADOR.has(valor)) return valor;
  try {
    const url = new URL(valor);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return limpo(`${url.origin}${url.pathname}`);
    }
    return limpo(url.protocol.slice(0, -1));
  } catch {
    return valor ? "<invalid>" : "";
  }
}

// Da página só interessa o caminho. No link de acesso, o próprio caminho é a
// credencial (app/c/[token]): ele sai mascarado qualquer que seja o tamanho.
const LINK_DE_ACESSO = "/c/";
const PAGINAS_FIXAS_DO_LINK = new Set(["/c/continuar"]);

function caminho(valor: string): string {
  let pathname: string;
  try {
    pathname = new URL(valor).pathname;
  } catch {
    return valor ? "<invalid>" : "";
  }
  if (pathname.startsWith(LINK_DE_ACESSO) && !PAGINAS_FIXAS_DO_LINK.has(pathname)) {
    return `${LINK_DE_ACESSO}<token>`;
  }
  return limpo(pathname);
}

function limpo(valor: string): string {
  return semCredencial(valor).slice(0, MAX_CAMPO);
}

function semCredencial(valor: string): string {
  const saida: string[] = [];
  let trecho = "";
  for (const caractere of `${valor} `) {
    if (CARACTERES_DE_CREDENCIAL.has(caractere)) {
      trecho += caractere;
      continue;
    }
    saida.push(trecho.length >= TAMANHO_DE_CREDENCIAL ? "<REDACTED>" : trecho, caractere);
    trecho = "";
  }
  return saida.join("").slice(0, -1);
}

// Limite por processo, em janela fixa. Passou do limite, o relato é descartado
// e conta numa linha só por janela: o log não vira lixeira de quem quiser
// encher. Por ser global, uma enxurrada também esconde relato legítimo por até
// um minuto; no modo só-relatório isso é aceitável.
export const MAX_REPORTS_PER_WINDOW = 120;
export const RATE_WINDOW_MS = 60_000;

export type RateLimiter = {
  readonly aceitar: (agoraMs: number) => { aceito: boolean; descartadosNaJanelaAnterior: number };
};

export function criarLimite(maximo = MAX_REPORTS_PER_WINDOW, janelaMs = RATE_WINDOW_MS): RateLimiter {
  let inicio = -Infinity;
  let aceitos = 0;
  let descartados = 0;
  return {
    aceitar(agoraMs) {
      let anterior = 0;
      if (agoraMs - inicio >= janelaMs) {
        anterior = descartados;
        inicio = agoraMs;
        aceitos = 0;
        descartados = 0;
      }
      if (aceitos >= maximo) {
        descartados += 1;
        return { aceito: false, descartadosNaJanelaAnterior: anterior };
      }
      aceitos += 1;
      return { aceito: true, descartadosNaJanelaAnterior: anterior };
    }
  };
}
