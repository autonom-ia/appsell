// CSV das vendas (#562): RFC 4180 (vírgula, CRLF, aspas dobradas) e defesa
// contra injeção de fórmula. O título do produto é texto do vendedor: um
// "=HYPERLINK(...)" viraria fórmula ao abrir na planilha. Campo que começa
// com = + - @ tab ou CR ganha um apóstrofo na frente.
export interface LinhaDeVenda {
  sold_on: string | null;
  refunded_on: string | null;
  product_title: string;
  status: "approved" | "refunded";
  purchase_ref: string;
}

const INICIO_DE_FORMULA = ["=", "+", "-", "@", "\t", "\r"];
const PRECISA_DE_ASPAS = [",", '"', "\r", "\n"];
const SITUACAO: Record<LinhaDeVenda["status"], string> = {
  approved: "aprovada",
  refunded: "estornada"
};
const CABECALHO = ["data_da_venda", "data_do_estorno", "produto", "situacao", "compra"];

export function campoCsv(valor: string): string {
  const seguro = INICIO_DE_FORMULA.some((inicio) => valor.startsWith(inicio)) ? `'${valor}` : valor;
  if (!PRECISA_DE_ASPAS.some((caractere) => seguro.includes(caractere))) return seguro;
  return `"${seguro.split('"').join('""')}"`;
}

export function linhasDeVendaEmCsv(linhas: readonly LinhaDeVenda[]): string {
  const corpo = linhas.map((linha) => [
    linha.sold_on ?? "",
    linha.refunded_on ?? "",
    linha.product_title,
    SITUACAO[linha.status],
    linha.purchase_ref
  ]);
  return [CABECALHO, ...corpo].map((campos) => `${campos.map(campoCsv).join(",")}\r\n`).join("");
}
