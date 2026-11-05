// Atribuição da visita (#563): os campos UTM e o link da loja que trouxeram a
// pessoa, com a data da visita. Vale a última visita com UTM nos 7 dias.
//
// Nada aqui identifica a pessoa: sem IP, sem id de visitante, sem fingerprint.
// O cookie só é gravado depois do "Aceitar" (ver consent.ts), e quem grava é o
// banner, no navegador. Este módulo só monta e confere o valor.

export const COOKIE_ATRIBUICAO = "sell_utm";
export const JANELA_SEGUNDOS = 7 * 24 * 60 * 60;
export const CAMPOS_UTM = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_content",
  "utm_term"
] as const;
export const PARAMETRO_DO_LINK = "sl";

const TAMANHO_MAXIMO = 100;
const PARAMETRO_DA_DATA = "t";
// Tolerância para relógio do navegador um pouco adiantado.
const FOLGA_DO_RELOGIO_SEGUNDOS = 60;
const HEXADECIMAL = "0123456789abcdef";
const PARTES_DO_UUID = [8, 4, 4, 4, 12];

export type CampoUtm = (typeof CAMPOS_UTM)[number];

export interface Atribuicao {
  utm: Partial<Record<CampoUtm, string>>;
  link: string | null;
  visitadoEm: number;
}

function textoLimpo(valor: string | null): string | null {
  if (valor === null) return null;
  const texto = valor.trim();
  if (texto.length === 0 || texto.length > TAMANHO_MAXIMO) return null;
  for (const caractere of texto) {
    const codigo = caractere.codePointAt(0) ?? 0;
    if (codigo < 32 || codigo === 127) return null;
  }
  return texto;
}

export function uuidCanonico(valor: string | null): string | null {
  if (valor === null) return null;
  const texto = valor.toLowerCase();
  const partes = texto.split("-");
  if (partes.length !== PARTES_DO_UUID.length) return null;
  const formatoCerto = partes.every(
    (parte, indice) =>
      parte.length === PARTES_DO_UUID[indice] &&
      [...parte].every((caractere) => HEXADECIMAL.includes(caractere))
  );
  return formatoCerto ? texto : null;
}

function camposDe(parametros: URLSearchParams): Atribuicao["utm"] {
  const utm: Atribuicao["utm"] = {};
  for (const campo of CAMPOS_UTM) {
    const valor = textoLimpo(parametros.get(campo));
    if (valor !== null) utm[campo] = valor;
  }
  return utm;
}

// Lê a URL da página. Sem nenhum campo UTM e sem link da loja, não há o que
// atribuir: devolve null, e a atribuição anterior continua valendo.
export function atribuicaoDaUrl(parametros: URLSearchParams, agora: number): Atribuicao | null {
  const utm = camposDe(parametros);
  const link = uuidCanonico(parametros.get(PARAMETRO_DO_LINK));
  if (Object.keys(utm).length === 0 && link === null) return null;
  return { utm, link, visitadoEm: Math.floor(agora) };
}

export function serializarAtribuicao(atribuicao: Atribuicao): string {
  const parametros = new URLSearchParams();
  for (const campo of CAMPOS_UTM) {
    const valor = atribuicao.utm[campo];
    if (valor !== undefined) parametros.set(campo, valor);
  }
  if (atribuicao.link !== null) parametros.set(PARAMETRO_DO_LINK, atribuicao.link);
  parametros.set(PARAMETRO_DA_DATA, String(atribuicao.visitadoEm));
  return encodeURIComponent(parametros.toString());
}

// Lê o cookie. Valor estranho, data no futuro ou fora da janela de 7 dias:
// null. O valor vem do navegador e é conferido campo a campo, como a URL.
export function lerAtribuicao(valor: string | undefined, agora: number): Atribuicao | null {
  if (valor === undefined || valor.length === 0) return null;
  let texto: string;
  try {
    texto = decodeURIComponent(valor);
  } catch {
    return null;
  }
  const parametros = new URLSearchParams(texto);
  const data = Number(parametros.get(PARAMETRO_DA_DATA));
  if (!Number.isSafeInteger(data)) return null;
  if (data > agora + FOLGA_DO_RELOGIO_SEGUNDOS || agora - data > JANELA_SEGUNDOS) return null;
  const atribuicao = atribuicaoDaUrl(parametros, data);
  return atribuicao;
}
