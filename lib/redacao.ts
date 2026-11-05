// Redação de texto livre antes de ir para o log do web (#549). Mesmas regras
// de apps/api/src/appsell_api/redacao_pessoal.py, mais a regra de credencial
// por tamanho que o redator da API já tinha:
//
// - query string de URL (carrega token, e-mail e assinatura);
// - valor de cabeçalho sensível (authorization, cookie...), mesmo dentro de um
//   stack trace ou de um objeto impresso;
// - e-mail;
// - CPF e CNPJ, só quando o dígito verificador bate;
// - sequência de 32+ caracteres [A-Za-z0-9_-] (regra do Rodrigo).
//
// Tudo com método de string, sem regex. É rede de segurança, não garantia: a
// regra segue sendo não pôr dado pessoal em log.

export const REDIGIDO = "<REDACTED>";

const CARACTERES_DE_EMAIL = new Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-@");
const CARACTERES_DE_CREDENCIAL = new Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-");
const TAMANHO_DE_CREDENCIAL = 32;
const PONTUACAO_DE_DOCUMENTO = new Set(".-/");
const FIM_DE_URL = new Set(" \t\r\n\"'<>`)]},");
const CABECALHOS_SENSIVEIS = ["authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"];
const FIM_DE_VALOR_DE_CABECALHO = new Set("\r\n\"'");

export function redigirTexto(texto: string): string {
  let saida = semQuery(texto);
  saida = semCabecalhoSensivel(saida);
  saida = porTrecho(saida, (c) => CARACTERES_DE_EMAIL.has(c), redigirEmail);
  saida = porTrecho(saida, ehDigitoOuPontuacao, redigirDocumento);
  return porTrecho(saida, (c) => CARACTERES_DE_CREDENCIAL.has(c), (trecho) =>
    trecho.length >= TAMANHO_DE_CREDENCIAL && !pareceIdentificador(trecho) ? REDIGIDO : trecho
  );
}

// Mesmo critério da API (`_parece_identificador`): UUID e slug identificam
// asset, pedido e produto, e apagá-los tira do log o que diz do que o erro fala.
// Um token tem maiúscula ou um trecho longo sem separador; um slug, não.
const TRECHO_MAXIMO_DE_IDENTIFICADOR = 16;
const HEX = new Set("0123456789abcdefABCDEF");

function pareceIdentificador(trecho: string): boolean {
  if (uuidCanonico(trecho)) return true;
  if (trecho !== trecho.toLowerCase()) return false;
  return trecho.split("-").flatMap((parte) => parte.split("_"))
    .every((parte) => parte.length <= TRECHO_MAXIMO_DE_IDENTIFICADOR);
}

function uuidCanonico(trecho: string): boolean {
  const grupos = trecho.split("-");
  return grupos.length === 5 &&
    grupos.map((grupo) => grupo.length).join(",") === "8,4,4,4,12" &&
    grupos.every((grupo) => [...grupo].every((c) => HEX.has(c)));
}

function semQuery(texto: string): string {
  const partes: string[] = [];
  let resto = texto;
  for (let inicio = resto.indexOf("://"); inicio >= 0; inicio = resto.indexOf("://")) {
    let fim = inicio + 3;
    while (fim < resto.length && !FIM_DE_URL.has(resto[fim] ?? "")) fim += 1;
    let url = resto.slice(0, fim);
    const corte = url.indexOf("?", inicio);
    if (corte >= 0 && !resto.startsWith(REDIGIDO, corte + 1)) {
      url = `${url.slice(0, corte + 1)}${REDIGIDO}`;
    }
    partes.push(url);
    resto = resto.slice(fim);
  }
  partes.push(resto);
  return partes.join("");
}

function semCabecalhoSensivel(texto: string): string {
  const minusculo = texto.toLowerCase();
  const partes: string[] = [];
  let posicao = 0;
  for (;;) {
    const achado = proximoCabecalho(minusculo, posicao);
    if (!achado) break;
    const [fimDoNome, inicioDoValor] = achado;
    let fimDoValor = inicioDoValor;
    while (fimDoValor < texto.length && !FIM_DE_VALOR_DE_CABECALHO.has(texto[fimDoValor] ?? "")) {
      fimDoValor += 1;
    }
    partes.push(texto.slice(posicao, inicioDoValor));
    if (fimDoValor > inicioDoValor) partes.push(REDIGIDO);
    posicao = Math.max(fimDoValor, fimDoNome);
  }
  partes.push(texto.slice(posicao));
  return partes.join("");
}

function proximoCabecalho(minusculo: string, desde: number): [number, number] | null {
  const achados = CABECALHOS_SENSIVEIS
    .map((nome) => primeiroComValor(minusculo, nome, desde))
    .filter((achado): achado is [number, number] => achado !== null)
    .sort((a, b) => a[0] - b[0]);
  return achados[0] ?? null;
}

function primeiroComValor(minusculo: string, nome: string, desde: number): [number, number] | null {
  for (let inicio = minusculo.indexOf(nome, desde); inicio >= 0; inicio = minusculo.indexOf(nome, inicio + nome.length)) {
    const fim = inicio + nome.length;
    const anterior = inicio > 0 ? minusculo[inicio - 1] ?? " " : " ";
    const valor = inicioDoValor(minusculo, fim);
    if (!(alfanumerico(anterior) || anterior === "-") && valor !== null) return [fim, valor];
  }
  return null;
}

function inicioDoValor(minusculo: string, fimDoNome: number): number | null {
  let posicao = fimDoNome;
  if (minusculo[posicao] === "\"" || minusculo[posicao] === "'") posicao += 1;
  while (minusculo[posicao] === " ") posicao += 1;
  if (minusculo[posicao] !== ":" && minusculo[posicao] !== "=") return null;
  posicao += 1;
  while (minusculo[posicao] === " " || minusculo[posicao] === "\"" || minusculo[posicao] === "'") posicao += 1;
  return posicao;
}

function alfanumerico(caractere: string): boolean {
  return CARACTERES_DE_CREDENCIAL.has(caractere) && caractere !== "_" && caractere !== "-";
}

function ehDigitoOuPontuacao(caractere: string): boolean {
  return (caractere >= "0" && caractere <= "9") || PONTUACAO_DE_DOCUMENTO.has(caractere);
}

function porTrecho(texto: string, pertence: (c: string) => boolean, redigir: (trecho: string) => string): string {
  const partes: string[] = [];
  let trecho = "";
  for (const caractere of texto) {
    if (pertence(caractere)) {
      trecho += caractere;
      continue;
    }
    if (trecho) partes.push(redigir(trecho));
    trecho = "";
    partes.push(caractere);
  }
  if (trecho) partes.push(redigir(trecho));
  return partes.join("");
}

function redigirEmail(trecho: string): string {
  const arroba = trecho.lastIndexOf("@");
  if (arroba <= 0) return trecho;
  const dominio = trecho.slice(arroba + 1);
  let semPontasDePonto = dominio;
  while (semPontasDePonto.startsWith(".")) semPontasDePonto = semPontasDePonto.slice(1);
  while (semPontasDePonto.endsWith(".")) semPontasDePonto = semPontasDePonto.slice(0, -1);
  return semPontasDePonto.includes(".") ? REDIGIDO : trecho;
}

function redigirDocumento(trecho: string): string {
  const digitos = [...trecho].filter((c) => c >= "0" && c <= "9").map(Number);
  if (digitos.length === 11 && cpfValido(digitos)) return REDIGIDO;
  if (digitos.length === 14 && cnpjValido(digitos)) return REDIGIDO;
  return trecho;
}

function todosIguais(d: number[]): boolean {
  return d.every((valor) => valor === d[0]);
}

function cpfValido(d: number[]): boolean {
  if (todosIguais(d)) return false;
  for (const posicao of [9, 10]) {
    let soma = 0;
    for (let i = 0; i < posicao; i += 1) soma += (d[i] ?? 0) * (posicao + 1 - i);
    if (((soma * 10) % 11) % 10 !== d[posicao]) return false;
  }
  return true;
}

function cnpjValido(d: number[]): boolean {
  if (todosIguais(d)) return false;
  const pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
  for (const posicao of [12, 13]) {
    const usados = pesos.slice(13 - posicao);
    let soma = 0;
    for (let i = 0; i < posicao; i += 1) soma += (d[i] ?? 0) * (usados[i] ?? 0);
    const resto = soma % 11;
    if ((resto < 2 ? 0 : 11 - resto) !== d[posicao]) return false;
  }
  return true;
}
