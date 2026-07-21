export type DashboardDraftStatus = "draft" | "published" | "unpublished" | "archived";

export interface DashboardDraft {
  status: DashboardDraftStatus;
}

export interface DashboardCatalogMetrics {
  total: number;
  published: number;
  truncated: boolean;
}

// Archived drafts stay reachable from the catalog workspace but do not count as
// part of the seller's active catalog on the overview.
export function dashboardCatalogMetrics(
  drafts: readonly DashboardDraft[],
  truncated = false
): DashboardCatalogMetrics {
  const active = drafts.filter((draft) => draft.status !== "archived");
  return {
    total: active.length,
    published: active.filter((draft) => draft.status === "published").length,
    truncated
  };
}

export function formatProductCount(metrics: DashboardCatalogMetrics): string {
  const padded = String(metrics.total).padStart(2, "0");
  return metrics.truncated ? `${padded}+` : padded;
}
