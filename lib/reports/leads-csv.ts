import { campoCsv } from "./sales-csv";

// CSV "para marketing" da base de leads (#619): só quem pode receber e-mail
// opcional da loja agora (a API já filtra). RFC 4180 e defesa contra fórmula,
// pelo mesmo `campoCsv` do CSV de vendas: nome e título são texto livre. O BOM
// faz a planilha abrir os acentos certos.
export interface LinhaDeLead {
  name: string | null;
  email: string;
  product_title: string;
  source: string;
  access_status: string;
  since: string;
}

const BOM = "﻿";
const CABECALHO = ["nome", "email", "produto", "como_entrou", "acesso", "desde"];
const ORIGEM: Record<string, string> = {
  billing: "compra",
  free_access: "acesso gratuito",
  manual: "manual"
};
const ACESSO: Record<string, string> = {
  active: "ativo",
  revoked: "revogado",
  expired: "expirado"
};

export function linhasDeLeadEmCsv(linhas: readonly LinhaDeLead[]): string {
  const corpo = linhas.map((linha) => [
    linha.name ?? "",
    linha.email,
    linha.product_title,
    ORIGEM[linha.source] ?? linha.source,
    ACESSO[linha.access_status] ?? linha.access_status,
    linha.since
  ]);
  return BOM + [CABECALHO, ...corpo].map((campos) => `${campos.map(campoCsv).join(",")}\r\n`).join("");
}
