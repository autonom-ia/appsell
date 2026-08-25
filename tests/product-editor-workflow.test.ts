import { describe, expect, it } from "vitest";

import { workflowState } from "../app/components/product-editor-workspace";

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
