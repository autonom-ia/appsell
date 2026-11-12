import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { lessonReadiness, outlineProgress, type LessonAssetSummary } from "../lib/curriculum/lesson-readiness";
import { classifyLessonLink, suggestedLinkTitle } from "../lib/curriculum/lesson-link";
import { createUndoQueue } from "../lib/curriculum/undo-queue";
import { autosaveReducer, initialAutosave } from "../lib/curriculum/autosave";
import { exceedsBytes } from "../lib/curriculum/curriculum-api.client";
import {
  FORBIDDEN_SELLER_WORDS,
  JOURNEY_COPY,
  fileRefusal,
  uploadStage
} from "../lib/curriculum/journey-copy";
import { MAX_VIDEO_BYTES, PDF_MEDIA_TYPE, VIDEO_MEDIA_TYPE } from "../lib/asset-media";

/**
 * A lógica da jornada de montar o curso (#649), sem tela.
 *
 * A tela em si é provada pelo E2E `montar-curso.spec.ts`, no celular e no
 * computador. Aqui ficam as regras que a tela só desenha: quando a aula está
 * pronta, o que conta como vídeo do YouTube, quando o desfazer vira pedido de
 * verdade, quando salvar sozinho e o que dizer em cada estado.
 */

const pronto = (role: "course_content" | "support_material", media = VIDEO_MEDIA_TYPE) => ({
  status: "ready" as const,
  content_role: role,
  media_type: media
});

describe("quando a aula está pronta", () => {
  const vazio: LessonAssetSummary = { assets: [], uploads: [] };

  it("sem vídeo, PDF nem texto, falta o conteúdo", () => {
    expect(lessonReadiness({ content: "", summary: vazio }).state).toBe("missing");
  });

  it("um vídeo ou PDF pronto deixa a aula pronta", () => {
    expect(lessonReadiness({ content: "", summary: { assets: [pronto("course_content")], uploads: [] } }).state).toBe("ready");
    expect(lessonReadiness({ content: "", summary: { assets: [pronto("course_content", PDF_MEDIA_TYPE)], uploads: [] } }).state).toBe("ready");
  });

  it("só material para baixar não é o conteúdo da aula", () => {
    expect(lessonReadiness({ content: "", summary: { assets: [pronto("support_material", PDF_MEDIA_TYPE)], uploads: [] } }).state).toBe("missing");
  });

  it("aula só de texto conta como pronta e diz que é só texto", () => {
    const result = lessonReadiness({ content: "Leia o capítulo 2.", summary: vazio });
    expect(result.state).toBe("text_only");
    expect(result.done).toBe(true);
  });

  it("texto feito só de espaço não conta", () => {
    expect(lessonReadiness({ content: "  \n ", summary: vazio }).state).toBe("missing");
  });

  it("arquivo recebido e ainda sendo conferido aparece como quase lá", () => {
    const summary = { assets: [], uploads: [{ status: "verifying" as const }] };
    expect(lessonReadiness({ content: "", summary }).state).toBe("checking");
  });

  it("envio que ficou pela metade pede atenção, não finge que está enviando", () => {
    const summary = { assets: [], uploads: [{ status: "pending" as const }] };
    expect(lessonReadiness({ content: "", summary }).state).toBe("interrupted");
  });

  it("o envio desta aba manda mais que a foto do servidor", () => {
    const summary = { assets: [], uploads: [{ status: "pending" as const }] };
    const result = lessonReadiness({ content: "", summary, live: { phase: "uploading", progress: 42 } });
    expect(result.state).toBe("uploading");
    expect(result.progress).toBe(42);
  });

  it("um envio novo por cima de um vídeo pronto continua mostrando o envio", () => {
    const summary = { assets: [pronto("course_content")], uploads: [] };
    expect(lessonReadiness({ content: "", summary, live: { phase: "uploading", progress: 10 } }).state).toBe("uploading");
  });

  it("enquanto a lista ainda não chegou, não chuta", () => {
    expect(lessonReadiness({ content: "", summary: null }).state).toBe("unknown");
  });

  it("o andamento conta as prontas e aponta a primeira que falta", () => {
    const progress = outlineProgress([
      { id: "a", title: "Boas-vindas", readiness: lessonReadiness({ content: "", summary: { assets: [pronto("course_content")], uploads: [] } }) },
      { id: "b", title: "Caderno", readiness: lessonReadiness({ content: "", summary: vazio }) },
      { id: "c", title: "Nicho", readiness: lessonReadiness({ content: "", summary: vazio }) }
    ]);
    expect(progress).toEqual({ total: 3, done: 1, next: { id: "b", title: "Caderno" } });
  });
});

describe("o link colado vira link ou vídeo", () => {
  it("reconhece o YouTube em qualquer forma comum", () => {
    for (const url of [
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      "https://youtu.be/dQw4w9WgXcQ?si=abc",
      "youtu.be/dQw4w9WgXcQ",
      "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=10"
    ]) {
      expect(classifyLessonLink(url)).toEqual({ kind: "embed", provider: "youtube", videoId: "dQw4w9WgXcQ" });
    }
  });

  it("reconhece o Vimeo", () => {
    expect(classifyLessonLink("https://vimeo.com/76979871")).toEqual({ kind: "embed", provider: "vimeo", videoId: "76979871" });
  });

  it("qualquer outro endereço https vira link", () => {
    expect(classifyLessonLink("https://drive.google.com/file/d/abc/view")).toEqual({
      kind: "external",
      url: "https://drive.google.com/file/d/abc/view",
      host: "drive.google.com"
    });
  });

  it("endereço sem https:// ganha o https", () => {
    expect(classifyLessonLink("www.exemplo.com.br/apostila")).toMatchObject({ kind: "external", host: "exemplo.com.br" });
  });

  it("um link do YouTube que não aponta para um vídeo não vira vídeo quebrado", () => {
    expect(classifyLessonLink("https://www.youtube.com/@canal")).toMatchObject({ kind: "external" });
  });

  it("o que não é endereço é recusado", () => {
    expect(classifyLessonLink("")).toBeNull();
    expect(classifyLessonLink("minha apostila")).toBeNull();
    expect(classifyLessonLink("http://exemplo.com")).toBeNull();
    expect(classifyLessonLink("javascript:alert(1)")).toBeNull();
  });

  it("sugere um nome que a pessoa reconhece", () => {
    expect(suggestedLinkTitle({ kind: "embed", provider: "youtube", videoId: "dQw4w9WgXcQ" })).toBe("Vídeo do YouTube");
    expect(suggestedLinkTitle({ kind: "embed", provider: "vimeo", videoId: "76979871" })).toBe("Vídeo do Vimeo");
    expect(suggestedLinkTitle({ kind: "external", url: "https://exemplo.com.br/x", host: "exemplo.com.br" })).toBe("exemplo.com.br");
  });
});

describe("um endereço que só parece do YouTube não vira vídeo", () => {
  it.each([
    "https://youtube.com.exemplo-falso.com/watch?v=dQw4w9WgXcQ",
    "https://exemplo-falso.com/youtube.com/watch?v=dQw4w9WgXcQ",
    "https://exemplo-falso.com/?u=https://youtu.be/dQw4w9WgXcQ",
    "https://vimeo.com.exemplo-falso.com/76979871"
  ])("%s fica link comum, aberto em outra aba", (endereco) => {
    expect(classifyLessonLink(endereco)?.kind).toBe("external");
  });
});

describe("desfazer em vez de perguntar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("o pedido só sai depois do prazo", async () => {
    const commit = vi.fn(async () => undefined);
    const queue = createUndoQueue({ delayMs: 8_000 });
    queue.schedule({ id: "aula-1", commit, revert: vi.fn() });
    await vi.advanceTimersByTimeAsync(7_999);
    expect(commit).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("desfazer dentro do prazo devolve o item e nada é enviado", async () => {
    const commit = vi.fn(async () => undefined);
    const revert = vi.fn();
    const queue = createUndoQueue({ delayMs: 8_000 });
    queue.schedule({ id: "aula-1", commit, revert });
    expect(queue.undo("aula-1")).toBe(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(commit).not.toHaveBeenCalled();
    expect(revert).toHaveBeenCalledTimes(1);
  });

  it("uma segunda exclusão confirma a primeira na hora: só um aviso por vez", async () => {
    const primeira = vi.fn(async () => undefined);
    const segunda = vi.fn(async () => undefined);
    const queue = createUndoQueue({ delayMs: 8_000 });
    queue.schedule({ id: "a", commit: primeira, revert: vi.fn() });
    queue.schedule({ id: "b", commit: segunda, revert: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    expect(primeira).toHaveBeenCalledTimes(1);
    expect(segunda).not.toHaveBeenCalled();
    expect(queue.pendingId()).toBe("b");
  });

  it("sair da tela confirma o que estava pendente", async () => {
    const commit = vi.fn(async () => undefined);
    const queue = createUndoQueue({ delayMs: 8_000 });
    queue.schedule({ id: "a", commit, revert: vi.fn() });
    await queue.flush({ leaving: true });
    expect(commit).toHaveBeenCalledWith({ leaving: true });
    expect(queue.pendingId()).toBeNull();
  });

  it("se o pedido falhar, o item volta e quem chamou fica sabendo", async () => {
    const revert = vi.fn();
    const onFailure = vi.fn();
    const queue = createUndoQueue({ delayMs: 8_000, onFailure });
    queue.schedule({ id: "a", commit: async () => { throw new Error("rede"); }, revert });
    await vi.advanceTimersByTimeAsync(8_000);
    expect(revert).toHaveBeenCalledTimes(1);
    expect(onFailure).toHaveBeenCalledWith("a");
  });

  it("desfazer depois de enviado não faz nada", async () => {
    const revert = vi.fn();
    const queue = createUndoQueue({ delayMs: 8_000 });
    queue.schedule({ id: "a", commit: async () => undefined, revert });
    await vi.advanceTimersByTimeAsync(8_000);
    expect(queue.undo("a")).toBe(false);
    expect(revert).not.toHaveBeenCalled();
  });
});

describe("salvar sozinho", () => {
  it("digitar marca como pendente, e salvar leva o valor da hora", () => {
    let state = autosaveReducer(initialAutosave("Aula 1"), { type: "edit", value: "Aula um" });
    expect(state).toMatchObject({ status: "pending", value: "Aula um", saved: "Aula 1" });
    state = autosaveReducer(state, { type: "start" });
    expect(state).toMatchObject({ status: "saving", inFlight: "Aula um" });
  });

  it("o que foi digitado durante o salvamento não se perde", () => {
    let state = autosaveReducer(initialAutosave("A"), { type: "edit", value: "AB" });
    state = autosaveReducer(state, { type: "start" });
    state = autosaveReducer(state, { type: "edit", value: "ABC" });
    state = autosaveReducer(state, { type: "success", value: "AB" });
    expect(state).toMatchObject({ status: "pending", value: "ABC", saved: "AB" });
  });

  it("salvo de verdade só quando o servidor confirmou o valor atual", () => {
    let state = autosaveReducer(initialAutosave("A"), { type: "edit", value: "AB" });
    state = autosaveReducer(state, { type: "start" });
    state = autosaveReducer(state, { type: "success", value: "AB" });
    expect(state.status).toBe("saved");
  });

  it("voltar ao valor salvo não pede salvamento", () => {
    let state = autosaveReducer(initialAutosave("A"), { type: "edit", value: "AB" });
    state = autosaveReducer(state, { type: "edit", value: "A" });
    expect(state.status).toBe("idle");
  });

  it("falha guarda o texto e diz que não salvou", () => {
    let state = autosaveReducer(initialAutosave("A"), { type: "edit", value: "AB" });
    state = autosaveReducer(state, { type: "start" });
    state = autosaveReducer(state, { type: "failure", reason: "offline" });
    expect(state).toMatchObject({ status: "failed", value: "AB", saved: "A", reason: "offline" });
  });

  it("o servidor mudou o valor por fora: adota quando não há edição pendente", () => {
    const state = autosaveReducer(initialAutosave("A"), { type: "external", value: "Z" });
    expect(state).toMatchObject({ status: "idle", value: "Z", saved: "Z" });
    const editing = autosaveReducer(autosaveReducer(initialAutosave("A"), { type: "edit", value: "AB" }), { type: "external", value: "Z" });
    expect(editing).toMatchObject({ status: "pending", value: "AB", saved: "Z" });
  });

  it("o eco do nome sem espaço nas pontas não apaga o espaço que a pessoa digitou", () => {
    let state = autosaveReducer(initialAutosave("Curso"), { type: "edit", value: "Curso de " });
    state = autosaveReducer(state, { type: "start" });
    state = autosaveReducer(state, { type: "success", value: "Curso de " });
    state = autosaveReducer(state, { type: "external", value: "Curso de" });
    expect(state.value).toBe("Curso de ");
    state = autosaveReducer(state, { type: "edit", value: "Curso de m" });
    expect(state.value).toBe("Curso de m");
  });
});

function recusa(...args: Parameters<typeof fileRefusal>) {
  const resultado = fileRefusal(...args);
  if (!resultado.refused) throw new Error("esperava recusa");
  return resultado;
}

describe("o que a tela diz", () => {
  // Cobre os textos centrais da jornada. Os textos escritos direto nos
  // componentes são conferidos na tela pelo E2E "sem nenhuma palavra técnica".
  it("textos da jornada, fases do envio e recusas não usam termo técnico", () => {
    const textos = [
      ...Object.values(JOURNEY_COPY),
      ...(["hashing", "creating", "uploading", "confirming", "verifying", "delayed"] as const)
        .map((fase) => uploadStage(fase, 37).title + " " + uploadStage(fase, 37).detail),
      ...[
        recusa({ name: "aula.mov", size: 10, type: "video/quicktime" }, "course_content"),
        recusa({ name: "aula.mp4", size: MAX_VIDEO_BYTES + 1, type: VIDEO_MEDIA_TYPE }, "course_content")
      ].map((resultado) => `${resultado.title} ${resultado.fix}`)
    ].join("\n").toLowerCase();
    for (const palavra of FORBIDDEN_SELLER_WORDS) expect(textos).not.toContain(palavra);
  });

  it("nome e texto medem bytes, como o servidor: acento conta dois", () => {
    expect(exceedsBytes("a".repeat(160), 160)).toBe(false);
    expect(exceedsBytes("ç".repeat(80), 160)).toBe(false);
    expect(exceedsBytes("ç".repeat(81), 160)).toBe(true);
  });

  it("o envio tem três momentos que a pessoa entende", () => {
    expect(uploadStage("uploading", 42).title).toBe("Enviando 42%");
    expect(uploadStage("hashing", 0).title).toBe("Enviando 0%");
    expect(uploadStage("confirming", 100).title).toBe("Enviando 100%");
    expect(uploadStage("verifying", 100).title).toBe("Quase lá: conferindo o arquivo");
    expect(uploadStage("delayed", 100).title).toBe("Quase lá: conferindo o arquivo");
  });

  it("vídeo grande demais diz o tamanho, o limite e o que fazer", () => {
    const resultado = recusa({ name: "aula.mp4", size: 1_503_238_554, type: VIDEO_MEDIA_TYPE }, "course_content");
    expect(resultado.title).toBe("Este vídeo tem 1,4 GB. O limite é 512 MB.");
    expect(resultado.fix).toContain("720p");
    expect(resultado.fix).toContain("YouTube");
  });

  it("vídeo do iPhone explica como exportar", () => {
    const resultado = recusa({ name: "IMG_0001.MOV", size: 10_000, type: "video/quicktime" }, "course_content");
    expect(resultado.title).toBe("Vídeos .MOV do iPhone ainda não entram.");
    expect(resultado.fix).toContain("MP4");
    expect(resultado.fix).toContain("YouTube");
  });

  it("formato que não cabe no lugar diz onde cabe", () => {
    const documento = recusa({ name: "apostila.docx", size: 1_000, type: "" }, "course_content");
    expect(documento.fix).toContain("Materiais para o aluno");
    const video = recusa({ name: "aula.mp4", size: 1_000, type: VIDEO_MEDIA_TYPE }, "support_material");
    expect(video.fix).toContain("Vídeo ou PDF da aula");
  });

  it("arquivo aceito não é recusado", () => {
    expect(fileRefusal({ name: "aula.mp4", size: 1_000, type: VIDEO_MEDIA_TYPE }, "course_content").refused).toBe(false);
  });
});
