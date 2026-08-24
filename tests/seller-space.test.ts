import { describe, expect, it } from "vitest";

import { autoSelectableSpace, SellerSpace, sellerRoleLabel } from "@/app/components/seller-space";

function space(overrides: Partial<SellerSpace> = {}): SellerSpace {
  return {
    id: "52000000-0000-4000-8000-000000000001",
    slug: "studio-unico",
    display_name: "Studio Único",
    role: "seller_owner",
    ...overrides
  };
}

describe("autoSelectableSpace", () => {
  it("selects the only space of a seller owner", () => {
    const only = space();

    expect(autoSelectableSpace([only])).toBe(only);
  });

  it("selects the only space of a team member", () => {
    const only = space({ role: "seller_editor" });

    expect(autoSelectableSpace([only])).toBe(only);
  });

  it("never selects for platform superadmin, even with a single active tenant", () => {
    // The superadmin reaches every active tenant, so a list of one is an
    // accident of the current data, not an unambiguous scope.
    const single = space({ role: "platform_superadmin" });

    expect(autoSelectableSpace([single])).toBeNull();
  });

  it("keeps the choice explicit when the identity reaches more than one space", () => {
    const spaces = [
      space(),
      space({ id: "52000000-0000-4000-8000-000000000002", slug: "studio-b", display_name: "Studio B", role: "seller_admin" })
    ];

    expect(autoSelectableSpace(spaces)).toBeNull();
  });

  it("returns null while the list is unknown or empty", () => {
    expect(autoSelectableSpace(null)).toBeNull();
    expect(autoSelectableSpace([])).toBeNull();
  });
});

describe("sellerRoleLabel", () => {
  it("keeps the labels used by the active spaces summary", () => {
    expect(sellerRoleLabel("owner")).toBe("proprietário");
    expect(sellerRoleLabel("seller_owner")).toBe("proprietário");
    expect(sellerRoleLabel("seller_admin")).toBe("administrador");
    expect(sellerRoleLabel("seller_editor")).toBe("membro da equipe");
  });

  it("labels the platform superadmin explicitly", () => {
    expect(sellerRoleLabel("platform_superadmin")).toBe("superadmin da plataforma");
  });
});
