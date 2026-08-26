import { describe, expect, it } from "vitest";

import {
  EXPIRY_PRESET_DAYS,
  SEAT_PRESETS,
  earliestExpiryDate,
  endOfDay,
  expiresAtFrom,
  isSeatPreset,
  maxUsesFrom
} from "../lib/access-class";
import { SEAT_RANGE } from "../lib/numeric-field";

// Um instante fixo, para a conta não depender do relógio de quem roda.
const NOON = new Date(2026, 7, 26, 12, 0, 0).getTime();
const DAY = 24 * 60 * 60 * 1000;

describe("presets de vagas", () => {
  it("cabem todos na faixa que a API aceita", () => {
    // Um preset fora da faixa seria um botão que promete o que o servidor
    // recusa — o mesmo defeito da #253, só que com a nossa própria lista.
    for (const preset of SEAT_PRESETS) {
      expect(preset).toBeGreaterThanOrEqual(SEAT_RANGE.min);
      expect(preset).toBeLessThanOrEqual(SEAT_RANGE.max);
    }
  });

  it("reconhece o próprio preset", () => {
    expect(isSeatPreset("25")).toBe(true);
    expect(isSeatPreset("26")).toBe(false);
  });
});

describe("maxUsesFrom", () => {
  it("manda vazio quando é sem limite — vazio é a escolha, não a falta dela", () => {
    expect(maxUsesFrom({ kind: "unlimited" }, "40")).toBe("");
  });

  it("manda o preset escolhido", () => {
    expect(maxUsesFrom({ kind: "preset", seats: 50 }, "")).toBe("50");
  });

  it("manda o que foi digitado quando é outro número", () => {
    expect(maxUsesFrom({ kind: "custom" }, " 37 ")).toBe("37");
  });

  it("recusa um preset que saísse da faixa", () => {
    // A lista é código, e código muda. Se alguém acrescentar 200000 amanhã,
    // isto vira vazio em vez de virar um 400 na cara do vendedor.
    expect(maxUsesFrom({ kind: "preset", seats: SEAT_RANGE.max + 1 }, "")).toBe("");
  });
});

describe("expiresAtFrom", () => {
  it("sem prazo é vazio", () => {
    expect(expiresAtFrom({ kind: "never" }, NOON)).toBe("");
  });

  it("conta os dias a partir de agora", () => {
    for (const days of EXPIRY_PRESET_DAYS) {
      expect(Date.parse(expiresAtFrom({ kind: "days", days }, NOON))).toBe(NOON + days * DAY);
    }
  });

  it("o prazo sempre cai no futuro, que é o que a rota exige", () => {
    // `parseExpiry` na rota recusa qualquer data que não seja maior que agora.
    for (const days of EXPIRY_PRESET_DAYS) {
      expect(Date.parse(expiresAtFrom({ kind: "days", days }, NOON))).toBeGreaterThan(NOON);
    }
  });
});

describe("endOfDay", () => {
  it("vale até o fim do dia escolhido, não até o começo dele", () => {
    // Meia-noite fecharia a turma antes do dia que a pessoa quis incluir.
    const closes = new Date(endOfDay("2026-09-30"));
    expect(closes.getFullYear()).toBe(2026);
    expect(closes.getMonth()).toBe(8);
    expect(closes.getDate()).toBe(30);
    expect(closes.getHours()).toBe(23);
    expect(closes.getMinutes()).toBe(59);
  });

  it("recusa um dia que não existe em vez de rolar para o mês seguinte", () => {
    // `new Date(2026, 1, 31)` vira 3 de março sem reclamar. Gravar isso seria
    // fechar a turma num dia que ninguém escolheu.
    expect(endOfDay("2026-02-31")).toBe("");
    expect(endOfDay("2026-13-01")).toBe("");
  });

  it("recusa o que não for uma data", () => {
    expect(endOfDay("")).toBe("");
    expect(endOfDay("amanhã")).toBe("");
    expect(endOfDay("2026-09-30T10:00")).toBe("");
  });

  it("aceita 29 de fevereiro em ano bissexto", () => {
    expect(endOfDay("2028-02-29")).not.toBe("");
  });
});

describe("earliestExpiryDate", () => {
  it("é hoje, no fuso de quem está olhando", () => {
    expect(earliestExpiryDate(NOON)).toBe("2026-08-26");
  });

  it("aceita o próprio piso: escolher hoje ainda fecha no futuro", () => {
    // Se o piso fosse recusado pela rota, o calendário estaria oferecendo um
    // dia que não dá para usar.
    expect(Date.parse(endOfDay(earliestExpiryDate(NOON)))).toBeGreaterThan(NOON);
  });
});
