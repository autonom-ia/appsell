import { describe, expect, it } from "vitest";

import { linhasDeVendaEmCsv, campoCsv } from "../lib/reports/sales-csv";
import { relatorioDeVendasLiberado } from "../lib/reports/sales-pilot";

describe("CSV de vendas (RFC 4180 + injeção de fórmula)", () => {
  it("põe aspas quando há vírgula, aspas, CR ou LF, e dobra as aspas", () => {
    expect(campoCsv("simples")).toBe("simples");
    expect(campoCsv("a,b")).toBe('"a,b"');
    expect(campoCsv('diz "oi"')).toBe('"diz ""oi"""');
    expect(campoCsv("linha\nnova")).toBe('"linha\nnova"');
    expect(campoCsv("volta\rcarro")).toBe('"volta\rcarro"');
  });

  it("neutraliza início de fórmula: = + - @ tab e CR", () => {
    for (const perigoso of ["=1+1", "+cmd", "-2", "@SUM(A1)", "\tx", "\rx"]) {
      const campo = campoCsv(perigoso);
      const semAspas = campo.startsWith('"') ? campo.slice(1) : campo;
      expect(semAspas.startsWith("'")).toBe(true);
    }
    expect(campoCsv("Curso = bom")).toBe("Curso = bom");
  });

  it("monta cabeçalho, uma linha por compra e termina linhas em CRLF", () => {
    const csv = linhasDeVendaEmCsv([
      { sold_on: "2026-10-01", refunded_on: null, product_title: "Curso, A", status: "approved", purchase_ref: "abcdef012345" },
      { sold_on: "2026-10-02", refunded_on: "2026-10-03", product_title: "=HYPERLINK()", status: "refunded", purchase_ref: "0123456789ab" }
    ]);
    expect(csv).toBe(
      "data_da_venda,data_do_estorno,produto,situacao,compra\r\n" +
      '2026-10-01,,"Curso, A",aprovada,abcdef012345\r\n' +
      "2026-10-02,2026-10-03,'=HYPERLINK(),estornada,0123456789ab\r\n"
    );
  });
});

describe("piloto do relatório de vendas", () => {
  const LOJA = "44444444-4444-4444-8444-444444444444";
  it("desligado sem a variável, e ligado só para as lojas da lista", () => {
    expect(relatorioDeVendasLiberado(LOJA, {})).toBe(false);
    expect(relatorioDeVendasLiberado(LOJA, { APPSELL_SALES_REPORT_PILOT_SELLER_IDS: "" })).toBe(false);
    expect(relatorioDeVendasLiberado(LOJA, { APPSELL_SALES_REPORT_PILOT_SELLER_IDS: ` x , ${LOJA} ` })).toBe(true);
    expect(relatorioDeVendasLiberado("outra", { APPSELL_SALES_REPORT_PILOT_SELLER_IDS: LOJA })).toBe(false);
  });
});
