// Redação de texto livre antes de ir para o log do web (#549). Mesmas regras
// de apps/api/src/appsell_api/redacao_pessoal.py, mais a regra de credencial
// por tamanho que o redator da API já tinha:
//
// - query string e fragmento de URL, absoluta ou caminho (`/c/x?token=...`);
// - valor de cabeçalho sensível (authorization, cookie...), mesmo dentro de um
//   stack trace ou de um objeto impresso pelo Node (lista, Map);
// - e-mail, inclusive com acento e com `%40` no lugar do `@`;
// - CPF e CNPJ, só quando o dígito verificador bate;
// - sequência de 32+ caracteres [A-Za-z0-9_-] (regra do Rodrigo).
//
// Tudo com método de string, sem regex. Cada regra varre o texto uma vez só, e
// o teto vem antes de qualquer regra. É rede de segurança, não garantia: a
// regra segue sendo não pôr dado pessoal em log.

export const REDIGIDO = "<REDACTED>";

const CARACTERES_DE_CREDENCIAL = new Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-");
const TAMANHO_DE_CREDENCIAL = 32;
const PONTUACAO_DE_EMAIL = new Set("._%+-@");
const ARROBA_CODIFICADA = "%40";
const PONTUACAO_DE_DOCUMENTO = new Set(".-/");
const FIM_DE_URL = new Set(" \t\r\n\"'<>`)]},");
const ESPACOS = [" ", "\t", "\r", "\n"];
const MARCA_DE_CORTE = " [...truncated...] ";
const ASPAS = new Set("\"'");
const INICIO_DE_LINHA_DE_PILHA = ["File ", "at "];

export const CABECALHOS_SENSIVEIS: ReadonlySet<string> = new Set([
  "authorization",
  "proxy-authorization",
  "cookie",
  "set-cookie",
  "x-api-key"
]);

// Teto de um texto antes de redigir, o mesmo da API (`TETO_DE_TEXTO`).
export const TETO_DE_TEXTO = 16_384;

export function redigirTexto(texto: string, teto = TETO_DE_TEXTO): string {
  let saida = limitarTamanho(texto, teto);
  saida = semQuery(saida);
  saida = semCabecalhoSensivel(saida);
  saida = porTrecho(saida, parteDeEmail, redigirEmail);
  saida = porTrecho(saida, ehDigitoOuPontuacao, redigirDocumento);
  return porTrecho(saida, (c) => CARACTERES_DE_CREDENCIAL.has(c), (trecho) =>
    trecho.length >= TAMANHO_DE_CREDENCIAL && !pareceIdentificador(trecho) ? REDIGIDO : trecho
  );
}

export function ehCabecalhoSensivel(nome: string): boolean {
  let normalizado = nome.toLowerCase().split("_").join("-");
  if (normalizado.startsWith("http-")) normalizado = normalizado.slice("http-".length);
  return CABECALHOS_SENSIVEIS.has(normalizado);
}

// Corta o meio de um texto grande demais, nas fronteiras de espaço. Cortar no
// meio de uma palavra deixaria um e-mail pela metade (`ana@exem`), que já não
// parece e-mail. O resultado nunca passa do teto.
export function limitarTamanho(texto: string, teto: number): string {
  if (texto.length <= teto) return texto;
  const tamanhoDoComeco = Math.floor(teto / 4);
  const tamanhoDoFim = Math.max(0, teto - tamanhoDoComeco - MARCA_DE_CORTE.length);
  let comeco = texto.slice(0, tamanhoDoComeco);
  let fim = tamanhoDoFim > 0 ? texto.slice(texto.length - tamanhoDoFim) : "";
  const ultimoEspaco = Math.max(...ESPACOS.map((espaco) => comeco.lastIndexOf(espaco)));
  comeco = ultimoEspaco >= 0 ? comeco.slice(0, ultimoEspaco + 1) : "";
  const posicoes = ESPACOS.map((espaco) => fim.indexOf(espaco)).filter((p) => p >= 0);
  fim = posicoes.length > 0 ? fim.slice(Math.min(...posicoes)) : "";
  return `${comeco}${MARCA_DE_CORTE}${fim}`.slice(0, teto);
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
  let posicao = 0;
  let indice = 0;
  while (indice < texto.length) {
    if (FIM_DE_URL.has(texto[indice] ?? "")) {
      indice += 1;
      continue;
    }
    let fim = indice + 1;
    while (fim < texto.length && !FIM_DE_URL.has(texto[fim] ?? "")) fim += 1;
    const corte = corteDaUrl(texto.slice(indice, fim));
    if (corte !== null) {
      const absoluto = indice + corte;
      partes.push(texto.slice(posicao, absoluto + 1));
      // A segunda passada vê `?<REDACTED>` e não repete.
      if (!(absoluto + 1 === fim && texto.startsWith(REDIGIDO, fim))) partes.push(REDIGIDO);
      posicao = fim;
    }
    indice = fim;
  }
  partes.push(texto.slice(posicao));
  return partes.join("");
}

function corteDaUrl(trecho: string): number | null {
  const esquema = trecho.indexOf("://");
  const base = esquema >= 0 ? esquema + 3 : 0;
  const cortes = ["?", "#"].map((marca) => trecho.indexOf(marca, base)).filter((p) => p >= 0);
  if (cortes.length === 0) return null;
  const corte = Math.min(...cortes);
  if (esquema < 0 && !trecho.slice(0, corte).includes("/")) return null;
  return corte;
}

function semCabecalhoSensivel(texto: string): string {
  const partes: string[] = [];
  let posicao = 0;
  let indice = 0;
  while (indice < texto.length) {
    if (!parteDeNome(texto[indice] ?? "")) {
      indice += 1;
      continue;
    }
    let fimDoNome = indice + 1;
    while (fimDoNome < texto.length && parteDeNome(texto[fimDoNome] ?? "")) fimDoNome += 1;
    const valor = CABECALHOS_SENSIVEIS.has(texto.slice(indice, fimDoNome).toLowerCase())
      ? valorDoCabecalho(texto, fimDoNome)
      : null;
    if (!valor) {
      indice = fimDoNome;
      continue;
    }
    partes.push(texto.slice(posicao, valor[0]), REDIGIDO);
    posicao = indice = valor[1];
  }
  partes.push(texto.slice(posicao));
  return partes.join("");
}

// Formatos: `Nome: valor`, `nome=valor`, `"nome": "valor"`, `'nome' => 'valor'`
// (Map), `(b'nome', b'valor')`, `'nome': [ 'a' ]` (lista do Node) e valor
// continuado na linha seguinte.
function valorDoCabecalho(texto: string, fimDoNome: number): [number, number] | null {
  let posicao = fimDoNome;
  const nomeEntreAspas = ASPAS.has(texto[posicao] ?? "");
  if (nomeEntreAspas) posicao += 1;
  posicao = depoisDeEspacos(texto, posicao);
  let soComAspas = false;
  if (texto.startsWith("=>", posicao)) posicao += 2;
  else if (texto[posicao] === ":" || texto[posicao] === "=") posicao += 1;
  else if (nomeEntreAspas && texto[posicao] === ",") {
    // `(b'authorization', b'...')`. Sem aspas, "authorization, cookie" é prosa.
    posicao += 1;
    soComAspas = true;
  } else return null;
  posicao = depoisDeEspacos(texto, posicao);
  if (texto[posicao] === "[") {
    const fim = texto.indexOf("]", posicao);
    return intervalo(posicao + 1, fim >= 0 ? fim : texto.length);
  }
  if (texto[posicao] === "b" && ASPAS.has(texto[posicao + 1] ?? "")) posicao += 1;
  const aspa = texto[posicao] ?? "";
  if (ASPAS.has(aspa)) return intervalo(posicao + 1, fimEntreAspas(texto, posicao + 1, aspa));
  if (soComAspas) return null;
  return intervalo(posicao, fimSemAspas(texto, posicao));
}

function depoisDeEspacos(texto: string, posicao: number): number {
  let p = posicao;
  while (texto[p] === " " || texto[p] === "\t") p += 1;
  return p;
}

function intervalo(inicio: number, fim: number): [number, number] | null {
  return fim > inicio ? [inicio, fim] : null;
}

function fimEntreAspas(texto: string, inicio: number, aspa: string): number {
  let p = inicio;
  while (p < texto.length && texto[p] !== aspa && texto[p] !== "\r" && texto[p] !== "\n") {
    p += texto[p] === "\\" ? 2 : 1;
  }
  return Math.min(p, texto.length);
}

function fimSemAspas(texto: string, inicio: number): number {
  let p = inicio;
  while (p < texto.length) {
    const caractere = texto[p] ?? "";
    if (ASPAS.has(caractere)) return p;
    if (caractere === "\r" || caractere === "\n") {
      const continuacao = continuacaoDaLinha(texto, p);
      if (continuacao === null) return p;
      p = continuacao;
      continue;
    }
    p += 1;
  }
  return p;
}

// Início da linha seguinte quando ela continua o valor (começa com espaço ou
// tab). Linha de pilha (`    at f`, `  File "..."`) não é continuação.
function continuacaoDaLinha(texto: string, quebra: number): number | null {
  const seguinte = quebra + (texto.startsWith("\r\n", quebra) ? 2 : 1);
  if (texto[seguinte] !== " " && texto[seguinte] !== "\t") return null;
  const conteudo = depoisDeEspacos(texto, seguinte);
  if (INICIO_DE_LINHA_DE_PILHA.some((inicio) => texto.startsWith(inicio, conteudo))) return null;
  return seguinte;
}

function digitoAscii(caractere: string): boolean {
  return caractere >= "0" && caractere <= "9";
}

// Letra de qualquer alfabeto tem maiúscula diferente da minúscula: `é`, `ç`.
function letra(caractere: string): boolean {
  return caractere.toLowerCase() !== caractere.toUpperCase();
}

function parteDeNome(caractere: string): boolean {
  return letra(caractere) || digitoAscii(caractere) || caractere === "-";
}

function parteDeEmail(caractere: string): boolean {
  return letra(caractere) || digitoAscii(caractere) || caractere === "_" || PONTUACAO_DE_EMAIL.has(caractere);
}

function ehDigitoOuPontuacao(caractere: string): boolean {
  return digitoAscii(caractere) || PONTUACAO_DE_DOCUMENTO.has(caractere);
}

function porTrecho(texto: string, pertence: (c: string) => boolean, redigir: (trecho: string) => string): string {
  const partes: string[] = [];
  let posicao = 0;
  let indice = 0;
  while (indice < texto.length) {
    if (!pertence(texto[indice] ?? "")) {
      indice += 1;
      continue;
    }
    let fim = indice + 1;
    while (fim < texto.length && pertence(texto[fim] ?? "")) fim += 1;
    const trecho = texto.slice(indice, fim);
    const redigido = redigir(trecho);
    if (redigido !== trecho) {
      partes.push(texto.slice(posicao, indice), redigido);
      posicao = fim;
    }
    indice = fim;
  }
  partes.push(texto.slice(posicao));
  return partes.join("");
}

function redigirEmail(trecho: string): string {
  const normalizado = trecho.split(ARROBA_CODIFICADA).join("@");
  const arroba = normalizado.lastIndexOf("@");
  if (arroba <= 0) return trecho;
  let dominio = normalizado.slice(arroba + 1);
  while (dominio.startsWith(".")) dominio = dominio.slice(1);
  while (dominio.endsWith(".")) dominio = dominio.slice(0, -1);
  return dominio.includes(".") ? REDIGIDO : trecho;
}

function redigirDocumento(trecho: string): string {
  const digitos = [...trecho].filter(digitoAscii).map(Number);
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
