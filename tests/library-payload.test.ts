import { describe, expect, it } from "vitest";

import { isLibraryPayload } from "../app/components/library-workspace";
import {
  parseBuyerProductsPayload,
  parseBuyerSellerProductsPayload
} from "../lib/buyer-products";
import {
  isLearningPlayerResponse,
  isProgressMutationResponse
} from "../app/components/learning-player";

const entitlementId = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const lessonId = "a39181dd-c70c-57b3-935d-d97fd834d709";
const assetId = "a6f01626-8b83-55e5-becc-fbab785f1493";
const sellerId = "90718777-93de-5e7a-aaa4-45b25051582c";
const productId = "9f004a04-0684-564e-9062-544143fd12d4";
const publicationId = "5ef7d0de-e57a-59a4-b405-6ee86c095f2a";

function libraryItem(overrides: Record<string, unknown> = {}) {
  return {
    entitlement_id: entitlementId,
    resume_lesson_id: lessonId,
    seller: { id: sellerId, slug: "autonomia", name: "Autonom.ia" },
    product: {
      id: productId, slug: "codex", title: "Codex na prática", description: "Curso",
      publication_id: publicationId, publication_version: 1
    },
    progress_percent: 0,
    ...overrides
  };
}

function playerResponse() {
  const selectedAsset = {
    href: `/api/library/items/${entitlementId}/assets/${assetId}`,
    title: "Material",
    media_type: "application/pdf"
  };
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
      selected_asset: selectedAsset,
      selected_assets: [selectedAsset]
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

  it("keeps an unavailable item and an empty description from invalidating other products", () => {
    const unavailable = libraryItem({
      entitlement_id: "c7b7267b-b10d-48fd-8cce-bff9e80c928c",
      resume_lesson_id: null,
      product: {
        id: "d8874d7b-c2d1-5f7e-83a6-81a493f7e2af", slug: "empty",
        title: "Curso em preparação", description: "",
        publication_id: "90b7dc42-c619-5e92-b6f2-da84150abf90", publication_version: 1
      }
    });
    expect(isLibraryPayload({ items: [libraryItem(), unavailable] })).toBe(true);
  });

  it("rejects unexpected fields and duplicate seller-product pairs", () => {
    expect(parseBuyerProductsPayload({ items: [libraryItem()], extra: true })).toBeNull();
    expect(parseBuyerProductsPayload({
      items: [libraryItem(), libraryItem({ entitlement_id: "c7b7267b-b10d-48fd-8cce-bff9e80c928c" })]
    })).toBeNull();
    expect(parseBuyerProductsPayload({
      items: [libraryItem({ seller: { id: "seller", slug: "bad", name: "Bad" } })]
    })).toBeNull();
  });

  it("accepts one seller storefront and rejects mixed sellers or extra keys", () => {
    const first = libraryItem();
    const storefrontItem = { seller: first.seller, product: first.product };
    expect(parseBuyerSellerProductsPayload({ items: [storefrontItem] })).toEqual({
      items: [storefrontItem]
    });
    expect(parseBuyerSellerProductsPayload({
      items: [storefrontItem, {
        seller: {
          id: "cc70ca23-6713-5850-b1a7-aa7abca7f451",
          slug: "seller-b",
          name: "Seller B"
        },
        product: {
          ...first.product,
          id: "d8874d7b-c2d1-5f7e-83a6-81a493f7e2af"
        }
      }]
    })).toBeNull();
    expect(parseBuyerSellerProductsPayload({ items: [{ ...storefrontItem, entitlement_id: entitlementId }] })).toBeNull();
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

    const duplicateAssets = playerResponse();
    duplicateAssets.player.selected_assets.push({ ...duplicateAssets.player.selected_assets[0]! });
    expect(isLearningPlayerResponse(duplicateAssets)).toBe(false);

    const mismatchedLegacyAsset = playerResponse();
    mismatchedLegacyAsset.player.selected_asset = {
      ...mismatchedLegacyAsset.player.selected_asset,
      title: "Outro material"
    };
    expect(isLearningPlayerResponse(mismatchedLegacyAsset)).toBe(false);
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
