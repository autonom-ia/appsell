import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * O payload que a produção devolve de verdade, atravessando as duas fronteiras.
 *
 * A fixture é a saída literal de `app_private.read_learning_player_v3` para a
 * aula que não abria — ids, títulos e endereços trocados por sintéticos, e
 * **nenhuma chave alterada**. É a forma real, não a minha reconstrução dela.
 *
 * Isso importa porque as duas correções anteriores falharam pelo mesmo motivo:
 * eu montava a fixture à mão, ela concordava com o validador por construção, e
 * o defeito real vivia numa chave que eu não sabia que existia — `materials`,
 * os links que o vendedor cadastra na aula.
 *
 * `isLesson` usava `hasExactKeys` com seis chaves. A sétima reprovava a aula, o
 * curso e o envelope inteiro: 503, sem nada apontar para o link.
 */

vi.mock("server-only", () => ({}));

import { learningPlayerResponse } from "../app/api/library/items/_route-shared";
import { isLearningPlayerPayload } from "../app/components/learning-player";
import { embedSource } from "../lib/lesson-embed";
import { safeLessonLink } from "../lib/lesson-markup";

const FIXTURE = fileURLToPath(new URL("./fixtures/player-v3-aula-com-links.json", import.meta.url));

function payloadDeProducao() {
  return JSON.parse(readFileSync(FIXTURE, "utf-8"));
}

function peloBff(player: unknown) {
  return learningPlayerResponse({ player }, "session-1", { subject: "aluno" });
}

beforeEach(() => {
  process.env.APPSELL_CSRF_HMAC_KEY_B64 = randomBytes(32).toString("base64");
});

describe("a aula com links do vendedor abre", () => {
  it("a fixture é mesmo a forma de produção: aula com `materials` e seis materiais", () => {
    // Sem esta âncora, alguém "consertaria" a fixture removendo a chave que o
    // teste existe para exercitar, e ele passaria provando nada.
    const player = payloadDeProducao();
    const aula = player.course.modules[0].lessons[0];
    expect(Object.keys(aula).sort()).toContain("materials");
    expect(aula.materials).toHaveLength(2);
    expect(aula.materials.map((m: { kind: string }) => m.kind).sort()).toEqual(["embed", "external"]);
    expect(player.selected_assets).toHaveLength(6);
    expect(player.schema_version).toBe(3);
  });

  it("atravessa o BFF e o validador do navegador", async () => {
    const response = peloBff(payloadDeProducao());
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(isLearningPlayerPayload({ ...body.player, csrf: body.csrf })).toBe(true);
  });

  it("os links chegam intactos ao navegador", async () => {
    const body = await peloBff(payloadDeProducao()).json();
    const materiais = body.player.course.modules[0].lessons[0].materials;
    expect(materiais).toHaveLength(2);
    expect(materiais[0].kind).toBe("external");
    expect(materiais[1].video_id).toBe("Exemplo_1-A");
  });

  it("os dois links são de fato apresentáveis, não só válidos", () => {
    // Sem isto, a fixture podia carregar um `video_id` que o player descarta na
    // hora de montar o `src` — o teste passaria e o aluno não veria o vídeo.
    const player = payloadDeProducao();
    const [externo, embutido] = player.course.modules[0].lessons[0].materials;
    expect(safeLessonLink(externo.url)).not.toBeNull();
    expect(embedSource(embutido.provider, embutido.video_id)).toContain(embutido.video_id);
  });

  it("uma aula sem links continua válida", async () => {
    const player = payloadDeProducao();
    for (const modulo of player.course.modules) {
      for (const aula of modulo.lessons) delete aula.materials;
    }
    expect(peloBff(player).status).toBe(200);
  });

  it("uma aula com lista de links vazia é válida", async () => {
    const player = payloadDeProducao();
    player.course.modules[0].lessons[0].materials = [];
    expect(peloBff(player).status).toBe(200);
  });

  it("recusa link malformado, sem deixar de recusar", async () => {
    const casos: Record<string, unknown> = {
      "sem id": { kind: "external", position: 1, provider: null, title: "t", url: "https://e.test", video_id: null },
      "kind desconhecido": { id: "493e79d2-06bd-40f2-8c09-d50495f12560", kind: "iframe", position: 1, provider: null, title: "t", url: "https://e.test", video_id: null },
      "embed sem video_id": { id: "493e79d2-06bd-40f2-8c09-d50495f12560", kind: "embed", position: 1, provider: "youtube", title: "t", url: null, video_id: null },
      "externo sem url": { id: "493e79d2-06bd-40f2-8c09-d50495f12560", kind: "external", position: 1, provider: null, title: "t", url: null, video_id: null },
      "chave a mais": { id: "493e79d2-06bd-40f2-8c09-d50495f12560", kind: "external", position: 1, provider: null, title: "t", url: "https://e.test", video_id: null, extra: 1 }
    };
    for (const [nome, material] of Object.entries(casos)) {
      const player = payloadDeProducao();
      player.course.modules[0].lessons[0].materials = [material];
      expect(peloBff(player).status, nome).toBe(503);
    }
  });

  it("uma aula com muitos links continua válida", async () => {
    // Nada no domínio limita a quantidade: nem a tabela, nem
    // `create_lesson_material_link`, nem a tela. Um teto inventado no BFF
    // recusaria uma aula que o produto deixa criar.
    const player = payloadDeProducao();
    const [modelo] = player.course.modules[0].lessons[0].materials;
    player.course.modules[0].lessons[0].materials = Array.from({ length: 250 }, (_, indice) => ({
      ...modelo,
      id: `493e79d2-06bd-40f2-8c09-${String(indice).padStart(12, "0")}`,
      position: indice + 1
    }));
    expect(peloBff(player).status).toBe(200);
  });

  it("recusa dois links com o mesmo id", async () => {
    const player = payloadDeProducao();
    const [primeiro] = player.course.modules[0].lessons[0].materials;
    player.course.modules[0].lessons[0].materials = [primeiro, { ...primeiro }];
    expect(peloBff(player).status).toBe(503);
  });
});
