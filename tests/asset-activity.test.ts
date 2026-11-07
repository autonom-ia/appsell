import { describe, expect, it } from "vitest";

import { mergeAssetActivity, type AssetActivity } from "../app/components/curriculum-workspace";
import { assetPanelKey } from "../app/components/lesson-asset-panel";

const LESSON_ID = "6a5b4c3d-2e1f-5a09-8b77-1c2d3e4f5a6b";
const OCIOSO: AssetActivity = { dirty: false, busy: false };
const ENVIANDO: AssetActivity = { dirty: true, busy: true };

function sujo(activity: Record<string, AssetActivity>) {
  return Object.values(activity).some((value) => value.dirty || value.busy);
}

describe("atividade dos painéis de anexos da aula (#612)", () => {
  it("os dois painéis da mesma aula têm chaves diferentes", () => {
    expect(assetPanelKey(LESSON_ID, "course_content")).not.toBe(assetPanelKey(LESSON_ID, "support_material"));
  });

  it("um painel que fica ocioso não apaga o envio em curso no irmão", () => {
    const conteudo = assetPanelKey(LESSON_ID, "course_content");
    const anexos = assetPanelKey(LESSON_ID, "support_material");
    const enviandoAnexo = mergeAssetActivity({}, anexos, ENVIANDO);
    const depoisDoIrmao = mergeAssetActivity(enviandoAnexo, conteudo, OCIOSO);
    expect(sujo(depoisDoIrmao)).toBe(true);
    expect(depoisDoIrmao[anexos]).toEqual(ENVIANDO);
  });

  it("a tela só fica limpa quando os dois painéis terminam", () => {
    const conteudo = assetPanelKey(LESSON_ID, "course_content");
    const anexos = assetPanelKey(LESSON_ID, "support_material");
    let atividade = mergeAssetActivity({}, conteudo, ENVIANDO);
    atividade = mergeAssetActivity(atividade, anexos, ENVIANDO);
    atividade = mergeAssetActivity(atividade, conteudo, OCIOSO);
    expect(sujo(atividade)).toBe(true);
    atividade = mergeAssetActivity(atividade, anexos, OCIOSO);
    expect(atividade).toEqual({});
  });

  it("aviso repetido devolve o mesmo objeto e não renderiza de novo", () => {
    const anexos = assetPanelKey(LESSON_ID, "support_material");
    const atual = mergeAssetActivity({}, anexos, ENVIANDO);
    expect(mergeAssetActivity(atual, anexos, { ...ENVIANDO })).toBe(atual);
  });
});
