import { describe, expect, it } from "vitest";
import {
  dashboardCatalogMetrics,
  formatProductCount,
  type DashboardDraft
} from "../lib/dashboard-projection";

describe("dashboard catalog metrics", () => {
  it("returns zeroed metrics for an empty catalog", () => {
    expect(dashboardCatalogMetrics([])).toEqual({ total: 0, published: 0, truncated: false });
  });

  it("counts published drafts as a subset of the active total", () => {
    const drafts: DashboardDraft[] = [
      { status: "draft" },
      { status: "published" },
      { status: "published" },
      { status: "unpublished" }
    ];
    expect(dashboardCatalogMetrics(drafts)).toEqual({ total: 4, published: 2, truncated: false });
  });

  it("excludes archived drafts from the active catalog", () => {
    const drafts: DashboardDraft[] = [
      { status: "archived" },
      { status: "published" },
      { status: "archived" }
    ];
    expect(dashboardCatalogMetrics(drafts)).toEqual({ total: 1, published: 1, truncated: false });
  });

  it("propagates the truncation flag from bounded pagination", () => {
    expect(dashboardCatalogMetrics([{ status: "draft" }], true).truncated).toBe(true);
  });
});

describe("product count formatting", () => {
  it("pads small counts to two digits", () => {
    expect(formatProductCount({ total: 3, published: 1, truncated: false })).toBe("03");
  });

  it("keeps large counts unpadded", () => {
    expect(formatProductCount({ total: 120, published: 5, truncated: false })).toBe("120");
  });

  it("marks truncated counts explicitly instead of pretending precision", () => {
    expect(formatProductCount({ total: 400, published: 9, truncated: true })).toBe("400+");
  });
});
