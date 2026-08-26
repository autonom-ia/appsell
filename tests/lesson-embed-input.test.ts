import { describe, expect, it } from "vitest";

import { EMBED_ID_PATTERNS, embedIdFromInput, isEmbedId } from "../lib/lesson-embed";

describe("o identificador vindo de um link colado", () => {
  it("aceita o link exato que quebrou em produção", () => {
    // O vendedor colou isto e a tela pediu "apenas o identificador". Ele levou
    // o `si=`, que é rastreamento, e o vídeo foi gravado morto — sem aviso.
    expect(embedIdFromInput("youtube", "https://youtu.be/-vvGAKtV_Ek?si=LW2Oweh9T56VW1nR"))
      .toBe("-vvGAKtV_Ek");
  });

  it("recusa o parâmetro de rastreamento como se fosse identificador", () => {
    // 16 caracteres. O formato antigo (1 a 64) engolia sem reclamar.
    expect(isEmbedId("LW2Oweh9T56VW1nR", "youtube")).toBe(false);
  });

  it("aceita as formas que o YouTube publica", () => {
    for (const entrada of [
      "https://www.youtube.com/watch?v=-vvGAKtV_Ek",
      "https://www.youtube.com/watch?v=-vvGAKtV_Ek&t=42",
      "https://youtube.com/embed/-vvGAKtV_Ek",
      "https://www.youtube.com/shorts/-vvGAKtV_Ek",
      "https://www.youtube-nocookie.com/embed/-vvGAKtV_Ek",
      "youtu.be/-vvGAKtV_Ek",
      "-vvGAKtV_Ek"
    ]) {
      expect(embedIdFromInput("youtube", entrada), entrada).toBe("-vvGAKtV_Ek");
    }
  });

  it("aceita as formas do Vimeo", () => {
    expect(embedIdFromInput("vimeo", "https://vimeo.com/123456789")).toBe("123456789");
    expect(embedIdFromInput("vimeo", "https://player.vimeo.com/video/123456789")).toBe("123456789");
    expect(embedIdFromInput("vimeo", "123456789")).toBe("123456789");
  });

  it("não deixa o endereço do quadro ser desviado", () => {
    // O endereço é montado com um literal mais o identificador. Se algo com
    // barra, ponto ou dois-pontos passasse, o vendedor escolheria o destino.
    for (const hostil of [
      "https://evil.com/-vvGAKtV_Ek",
      "https://youtu.be/../../evil",
      "https://youtu.be/-vvGAKtV_Ek/../evil",
      "../evil",
      "a/b",
      "javascript:alert(1)",
      ""
    ]) {
      expect(embedIdFromInput("youtube", hostil), hostil).toBeNull();
    }
    expect(embedIdFromInput("vimeo", "https://evil.com/123456789")).toBeNull();
  });

  it("recusa provedor que não conhecemos", () => {
    expect(embedIdFromInput("dailymotion", "https://youtu.be/-vvGAKtV_Ek")).toBeNull();
    expect(embedIdFromInput(null, "-vvGAKtV_Ek")).toBeNull();
  });

  it("o formato por provedor é o mesmo que o banco exige na 0041", () => {
    expect(EMBED_ID_PATTERNS.youtube.source).toBe("^[A-Za-z0-9_-]{11}$");
    expect(EMBED_ID_PATTERNS.vimeo.source).toBe("^[0-9]{6,12}$");
  });
});
