import { describe, expect, it } from "vitest";

import { isLibraryPayload } from "../app/components/library-workspace";

describe("library payload validation", () => {
  it("rejects an incomplete nested course before render", () => {
    expect(isLibraryPayload({
      items: [{
        seller: { id: "seller", slug: "seller", name: "Seller" },
        product: {
          id: "product", slug: "product", title: "Product", description: "Description",
          publication_id: "publication", publication_version: 1
        },
        progress_percent: 0,
        assets: [],
        snapshot: { courses: [{}] }
      }]
    })).toBe(false);
  });
});
