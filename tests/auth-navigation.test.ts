import { describe, expect, test } from "vitest";

import { productCheckoutLoginHref } from "../lib/auth-navigation";

describe("auth navigation", () => {
  test("marks paid product checkout login as a new-user entry", () => {
    expect(productCheckoutLoginHref("/s/hub2you/p/condicoes-gerais"))
      .toBe("/auth/login?next=%2Fs%2Fhub2you%2Fp%2Fcondicoes-gerais&new_user=true");
  });
});
