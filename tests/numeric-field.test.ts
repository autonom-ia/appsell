import { describe, expect, it } from "vitest";

import {
  SEAT_RANGE,
  isRejectedNumericKey,
  keepDigits,
  keepPriceCharacters,
  readSeatInput,
  seatRangeMessage
} from "../lib/numeric-field";

// A faixa de vagas tem de continuar sendo a mesma que a API aplica. Se alguém
// mudar `max_uses: Field(ge=1, le=100_000)` em catalog_routes.py ou `MAX_SEATS`
// na rota do BFF sem mexer aqui, o campo passa a prometer o que o servidor
// recusa — que é exatamente o defeito que a #253 fecha.
describe("faixa de vagas", () => {
  it("espelha o limite da API", () => {
    expect(SEAT_RANGE.min).toBe(1);
    expect(SEAT_RANGE.max).toBe(100_000);
  });
});

describe("readSeatInput", () => {
  it("trata vazio como ilimitado, não como campo por preencher", () => {
    expect(readSeatInput("")).toEqual({ kind: "empty" });
    expect(readSeatInput("   ")).toEqual({ kind: "empty" });
  });

  it("aceita as duas pontas da faixa", () => {
    expect(readSeatInput("1")).toEqual({ kind: "ok", seats: 1 });
    expect(readSeatInput("100000")).toEqual({ kind: "ok", seats: 100_000 });
  });

  it("recusa zero e recusa acima do teto", () => {
    expect(readSeatInput("0").kind).toBe("below");
    expect(readSeatInput("100001").kind).toBe("above");
  });

  it("recusa texto colado no campo", () => {
    // A digitação é filtrada, mas colar continua sendo um caminho aberto.
    expect(readSeatInput("dez").kind).toBe("below");
    expect(readSeatInput("1e3").kind).toBe("below");
    expect(readSeatInput("-5").kind).toBe("below");
  });
});

describe("seatRangeMessage", () => {
  it("cala quando o valor serve", () => {
    expect(seatRangeMessage({ kind: "empty" })).toBeNull();
    expect(seatRangeMessage({ kind: "ok", seats: 40 })).toBeNull();
  });

  // O ponto da #253: a mensagem diz o limite. "Revise o campo destacado" era o
  // que a tela dizia antes, e não dizia nada.
  it("nomeia o limite quando o valor não serve", () => {
    expect(seatRangeMessage({ kind: "above" })).toContain("100.000");
    expect(seatRangeMessage({ kind: "below" })).toContain("1");
  });
});

describe("keepDigits", () => {
  it("descarta tudo que não for dígito", () => {
    expect(keepDigits("dez")).toBe("");
    expect(keepDigits("1e3")).toBe("13");
    expect(keepDigits("-40")).toBe("40");
    expect(keepDigits("1 000")).toBe("1000");
  });

  it("não deixa crescer além do número de dígitos do teto", () => {
    expect(keepDigits("999999999")).toBe("999999");
  });
});

describe("keepPriceCharacters", () => {
  it("descarta letras sem tocar nos dígitos", () => {
    expect(keepPriceCharacters("dez")).toBe("");
    expect(keepPriceCharacters("49,90 reais")).toBe("49,90");
  });

  it("mantém o separador que a pessoa escolheu", () => {
    // Vírgula e ponto são ambos aceitos por parsePriceInputToMinor; trocar um
    // pelo outro seria reescrever o que a pessoa digitou em um campo de dinheiro.
    expect(keepPriceCharacters("49,90")).toBe("49,90");
    expect(keepPriceCharacters("49.90")).toBe("49.90");
  });

  it("admite um único separador", () => {
    expect(keepPriceCharacters("1,2,3")).toBe("1,23");
  });

  it("corta em 9 inteiros e 2 casas, como o PRICE_INPUT da oferta", () => {
    expect(keepPriceCharacters("12345678901")).toBe("123456789");
    expect(keepPriceCharacters("10,999")).toBe("10,99");
  });

  it("não completa centavos por conta própria", () => {
    expect(keepPriceCharacters("49")).toBe("49");
    expect(keepPriceCharacters("49,")).toBe("49,");
  });
});

describe("isRejectedNumericKey", () => {
  // Sem isto, `type="number"` aceita "1e5" e devolve valor vazio ao React: o
  // campo parece apagar sozinho.
  it("barra o que type=number aceitaria e o React não veria", () => {
    for (const key of ["e", "E", "+", "-", ",", "."]) {
      expect(isRejectedNumericKey(key)).toBe(true);
    }
  });

  it("deixa passar dígito e as teclas de edição", () => {
    for (const key of ["1", "0", "Backspace", "ArrowLeft", "Tab", "Enter"]) {
      expect(isRejectedNumericKey(key)).toBe(false);
    }
  });
});
