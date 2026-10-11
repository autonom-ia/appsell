import { describe, expect, it } from "vitest";

import {
  isPublicSlug,
  parsePublicProductPage,
  parsePublicStorefront
} from "../lib/public-storefront";

const seller = { slug: "autonomia-demo", name: "Autonom.ia Demo" };
const card = {
  slug: "codex-na-pratica",
  title: "Codex na prática",
  description: "Curso público",
  course_count: 1,
  offer: { title: "Acesso Codex", summary: "Aprenda com segurança" }
};

describe("public storefront projection", () => {
  it("accepts only canonical ASCII slugs", () => {
    expect(isPublicSlug("autonomia-demo")).toBe(true);
    expect(isPublicSlug("Autonomia-Demo")).toBe(false);
    expect(isPublicSlug(" autonomia-demo")).toBe(false);
    expect(isPublicSlug("autonomia--demo")).toBe(false);
    expect(isPublicSlug("áutonomia-demo")).toBe(false);
  });

  it("accepts the exact allowlisted storefront response", () => {
    expect(parsePublicStorefront({ seller, products: [card], next_cursor: null })).toEqual({
      seller,
      products: [card],
      next_cursor: null
    });
  });

  it("preserves valid whitespace in public descriptions and summaries", () => {
    const spaced = {
      ...card,
      description: " descrição pública ",
      offer: { ...card.offer, summary: " resumo público " }
    };
    expect(parsePublicStorefront({ seller, products: [spaced], next_cursor: null })?.products[0])
      .toEqual(spaced);
  });

  it.each(["snapshot", "assets", "buyers", "seller_organization_id", "publication_id", "sha256"])(
    "rejects leaked field %s",
    (field) => {
      expect(parsePublicStorefront({
        seller,
        products: [{ ...card, [field]: "private" }],
        next_cursor: null
      })).toBeNull();
    }
  );

  it("rejects raw lesson content nested under courses", () => {
    expect(parsePublicProductPage({
      seller,
      product: {
        slug: card.slug,
        title: card.title,
        description: card.description,
        offer: card.offer,
        courses: [{ title: "Curso", description: "Público", position: 0, lessons: [] }]
      },
      other_products: []
    })).toBeNull();
  });

  it("rejects other products that duplicate the current product", () => {
    expect(parsePublicProductPage({
      seller,
      product: {
        slug: card.slug,
        title: card.title,
        description: card.description,
        offer: card.offer,
        courses: [{ title: "Curso", description: "Público", position: 0 }]
      },
      other_products: [{
        slug: card.slug,
        title: card.title,
        description: card.description,
        course_count: 1
      }]
    })).toBeNull();
  });
});
