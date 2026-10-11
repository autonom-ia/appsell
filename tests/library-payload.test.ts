import { describe, expect, it } from "vitest";

import { isLibraryPayload } from "../app/components/library-workspace";
import {
  isLearningPlayerResponse,
  isProgressMutationResponse
} from "../app/components/learning-player";

const entitlementId = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const lessonId = "a39181dd-c70c-57b3-935d-d97fd834d709";
const assetId = "a6f01626-8b83-55e5-becc-fbab785f1493";

function libraryItem(overrides: Record<string, unknown> = {}) {
  return {
    entitlement_id: entitlementId,
    resume_lesson_id: lessonId,
    seller: { id: "seller", slug: "autonomia", name: "Autonom.ia" },
    product: {
      id: "product", slug: "codex", title: "Codex na prática", description: "Curso",
      publication_id: "publication", publication_version: 1
    },
    progress_percent: 0,
    ...overrides
  };
}

function playerResponse() {
  return {
    player: {
      seller: { id: "seller" },
      product: { id: "product", slug: "codex", title: "Codex na prática", description: "Curso" },
      publication: { id: "publication", version: 1, source_sha256: "a".repeat(64) },
      entitlement_id: entitlementId,
      enrollment_id: "enrollment",
      course: {
        id: "course", title: "Codex na prática", description: "Curso", position: 0,
        modules: [{
          id: "module", title: "Fundamentos", status: "active", position: 0,
          lessons: [{ id: lessonId, title: "O que é Codex", kind: "text", content: "Conteúdo", status: "active", position: 0 }]
        }]
      },
      selected_lesson_id: lessonId,
      resume_lesson_id: lessonId,
      course_progress_percent: 0,
      state_version: 0,
      lesson_progress: [] as Array<{ lesson_id: string; progress_percent: number; completed: boolean; updated_at: string }>,
      selected_asset: { href: `/api/library/items/${entitlementId}/assets/${assetId}`, title: "Material", media_type: "application/pdf" }
    },
    csrf: { progress: "progress-token", complete: "complete-token" }
  };
}

describe("library payload validation", () => {
  it("accepts a minimal shelf item and rejects duplicate entitlements", () => {
    const item = libraryItem();
    expect(isLibraryPayload({ items: [item] })).toBe(true);
    expect(isLibraryPayload({ items: [item, item] })).toBe(false);
  });

  it("requires a server-selected resume lesson and a bounded progress", () => {
    expect(isLibraryPayload({ items: [libraryItem({ resume_lesson_id: "" })] })).toBe(false);
    expect(isLibraryPayload({ items: [libraryItem({ progress_percent: 101 })] })).toBe(false);
  });

  it("rejects seller and product slugs that could escape the public storefront route", () => {
    expect(isLibraryPayload({
      items: [libraryItem({ seller: { id: "seller", slug: "../admin", name: "Autonom.ia" } })]
    })).toBe(false);
    expect(isLibraryPayload({
      items: [libraryItem({
        product: {
          id: "product", slug: "//attacker.example", title: "Codex na prática", description: "Curso",
          publication_id: "publication", publication_version: 1
        }
      })]
    })).toBe(false);
  });

  it("keeps an unavailable item and an empty description from invalidating other products", () => {
    const unavailable = libraryItem({
      entitlement_id: "c7b7267b-b10d-48fd-8cce-bff9e80c928c",
      resume_lesson_id: null,
      product: {
        id: "product-empty", slug: "empty", title: "Curso em preparação", description: "",
        publication_id: "publication-empty", publication_version: 1
      }
    });
    expect(isLibraryPayload({ items: [libraryItem(), unavailable] })).toBe(true);
  });
});

describe("learning player payload validation", () => {
  it("accepts the BFF envelope and rejects a selected lesson outside the publication", () => {
    const response = playerResponse();
    expect(isLearningPlayerResponse(response)).toBe(true);
    response.player.selected_lesson_id = "foreign-lesson";
    expect(isLearningPlayerResponse(response)).toBe(false);
  });

  it("rejects duplicate lesson progress and external asset URLs", () => {
    const duplicate = playerResponse();
    duplicate.player.lesson_progress = [
      { lesson_id: lessonId, progress_percent: 1, completed: false, updated_at: "2026-07-11T12:00:00Z" },
      { lesson_id: lessonId, progress_percent: 1, completed: false, updated_at: "2026-07-11T12:00:00Z" }
    ];
    expect(isLearningPlayerResponse(duplicate)).toBe(false);
    const external = playerResponse();
    external.player.selected_asset.href = "https://attacker.example/material.pdf";
    expect(isLearningPlayerResponse(external)).toBe(false);
  });

  it("accepts optional copy and empty modules from a valid published snapshot", () => {
    const response = playerResponse();
    response.player.product.description = "";
    response.player.course.description = "";
    response.player.course.modules.unshift({
      id: "empty-module", title: "Materiais futuros", status: "active", position: 0, lessons: []
    });
    response.player.course.modules[1]!.lessons[0]!.content = "";
    expect(isLearningPlayerResponse(response)).toBe(true);
  });

  it("validates the mutation envelope and completion consistency", () => {
    const response = {
      progress: {
        entitlement_id: entitlementId,
        enrollment_id: "enrollment",
        lesson_id: lessonId,
        lesson_progress_percent: 100,
        course_progress_percent: 20,
        resume_lesson_id: lessonId,
        state_version: 2,
        applied: true
      }
    };
    expect(isProgressMutationResponse(response)).toBe(true);
    response.progress.course_progress_percent = 101;
    expect(isProgressMutationResponse(response)).toBe(false);
  });
});
