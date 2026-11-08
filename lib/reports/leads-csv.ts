import { campoCsv } from "./sales-csv";

// CSV "para marketing" da base de leads (#619): uma linha por pessoa, só quem
// pode receber e-mail opcional da loja agora (a API já filtra e agrega). O
// mesmo e-mail nunca sai duas vezes, e quem importa numa ferramenta de e-mail
// não manda em dobro. RFC 4180 e defesa contra fórmula, pelo mesmo `campoCsv`
// do CSV de vendas: nome e título são texto livre. O BOM faz a planilha abrir
// os acentos certos.
export interface LinhaDeLead {
  name: string | null;
  email: string;
  products: string;
  since: string;
}

const BOM = "﻿";
const CABECALHO = ["nome", "email", "produtos", "desde"];

export function linhasDeLeadEmCsv(linhas: readonly LinhaDeLead[]): string {
  const corpo = linhas.map((linha) => [linha.name ?? "", linha.email, linha.products, linha.since]);
  return BOM + [CABECALHO, ...corpo].map((campos) => `${campos.map(campoCsv).join(",")}\r\n`).join("");
}
