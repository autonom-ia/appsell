import { describe, expect, it } from "vitest";

import { workflowState } from "../app/components/product-editor-workspace";

describe("product editor workflow", () => {
  it("uses the server curriculum summary on a direct load", () => {
    const detail = {
      title: "IA na prática",
      slug: "ia-na-pratica",
      course: { title: "Especialista", id: "course", status: "draft", draft_version: 2 },
      offer: { id: "offer", title: "Oferta", summary: "", status: "draft", draft_version: 1 },
      publication: { id: "publication", version: 1 },
      has_unpublished_changes: false
    } as Parameters<typeof workflowState>[0];
    const result = workflowState(detail, { modules: 1, lessons: 1 }, false, "idle");

    expect(result.curriculo).toBe("complete");
    expect(result.preview).toBe("complete");
    expect(result.publicar).toBe("complete");
  });

  it("keeps a real empty curriculum pending", () => {
    const detail = {
      title: "Produto",
      slug: "produto",
      course: { title: "Curso", id: "course", status: "draft", draft_version: 1 },
      offer: { id: "offer", title: "Oferta", summary: "", status: "draft", draft_version: 1 },
      publication: null,
      has_unpublished_changes: true
    } as Parameters<typeof workflowState>[0];

    expect(workflowState(detail, { modules: 0, lessons: 0 }, false, "idle").curriculo)
      .toBe("pending");
  });
});
