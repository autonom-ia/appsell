import { describe, expect, it } from "vitest";

import { shouldFlushPendingDraft, workflowState } from "../app/components/product-editor-workspace";

/**
 * `workflowState` diz apenas se cada área já tem conteúdo, para a navegação
 * marcar o que está vazio. Não é portão: o que libera a publicação é
 * `detail.readiness`, calculado pelo servidor e mostrado na coluna de
 * prontidão. Estes testes existem para que a distinção não se perca.
 */
describe("marcadores de área do editor", () => {
  it("usa o resumo de currículo que veio do servidor", () => {
    const detail = {
      title: "IA na prática",
      slug: "ia-na-pratica",
      course: { title: "Especialista", id: "course", status: "draft", draft_version: 2 },
      offer: { id: "offer", title: "Oferta", summary: "", status: "draft", draft_version: 1 },
      publication: { id: "publication", version: 1 },
      has_unpublished_changes: false
    } as Parameters<typeof workflowState>[0];

    const result = workflowState(detail, { modules: 1, lessons: 1 });

    expect(result.produto).toBe("complete");
    expect(result.curriculo).toBe("complete");
    expect(result.publicar).toBe("complete");
  });

  it("mantém um currículo realmente vazio como área vazia", () => {
    const detail = {
      title: "Produto",
      slug: "produto",
      course: { title: "Curso", id: "course", status: "draft", draft_version: 1 },
      offer: { id: "offer", title: "Oferta", summary: "", status: "draft", draft_version: 1 },
      publication: null,
      has_unpublished_changes: true
    } as Parameters<typeof workflowState>[0];

    expect(workflowState(detail, { modules: 0, lessons: 0 }).curriculo).toBe("pending");
  });

  it("não tem mais área de prévia", () => {
    // A prévia virou o botão "Ver como aluno", disponível de qualquer área.
    const detail = {
      title: "Produto",
      slug: "produto",
      course: { title: "Curso", id: "course", status: "draft", draft_version: 1 },
      offer: { id: "offer", title: "Oferta", summary: "", status: "draft", draft_version: 1 },
      publication: null,
      has_unpublished_changes: true
    } as Parameters<typeof workflowState>[0];

    const result = workflowState(detail, { modules: 1, lessons: 1 });

    expect(Object.keys(result).sort()).toEqual(["curriculo", "oferta", "produto", "publicar"]);
  });
});

/**
 * Regressão registrada: quando o autosave ganhou o envio na saída da página, o
 * botão "Descartar e voltar" deixou de descartar. Ele não zerava o estado sujo,
 * então o envio de saída reenviava ao servidor exatamente a edição que o
 * vendedor tinha acabado de mandar jogar fora — e a tela já tinha dito que
 * descartou.
 */
describe("envio pendente na saída da página", () => {
  it("envia o que está sujo quando a página vai embora", () => {
    expect(shouldFlushPendingDraft({ dirty: true, discarded: false })).toBe(true);
  });

  it("não envia o que o vendedor mandou descartar", () => {
    expect(shouldFlushPendingDraft({ dirty: true, discarded: true })).toBe(false);
  });

  it("não envia nada quando não há alteração", () => {
    expect(shouldFlushPendingDraft({ dirty: false, discarded: false })).toBe(false);
    expect(shouldFlushPendingDraft({ dirty: false, discarded: true })).toBe(false);
  });
});
