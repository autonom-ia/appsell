import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

vi.mock("server-only", () => ({}));
vi.mock("../app/components/product-editor-workspace", () => ({ ProductEditorWorkspace: () => null }));

import ProductEditorPage from "../app/(protected)/products/[sellerId]/[productId]/page";

const originalEnvironment = { ...process.env };

type EditorProps = { usesFinancialCheckout: boolean };

async function usesFinancialCheckout() {
  const page = await ProductEditorPage({ params: Promise.resolve({ sellerId: "s", productId: "p" }) });
  const editor = (page as ReactElement<{ children: ReactElement<EditorProps> }>).props.children;
  return editor.props.usesFinancialCheckout;
}

// #544: sem provedor válido o editor não pode dar 500 e segue o contrato do
// checkout local, que exige conta de recebimento para publicar pago.
describe("editor de produto e o provedor do checkout", () => {
  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("abre sem a variável, ou com valor desconhecido, no contrato local", async () => {
    for (const value of [undefined, "unexpected"]) {
      if (value === undefined) delete process.env.APPSELL_CHECKOUT_PROVIDER;
      else process.env.APPSELL_CHECKOUT_PROVIDER = value;
      expect(await usesFinancialCheckout(), String(value)).toBe(false);
    }
  });

  it("segue o Financial só com o valor explícito", async () => {
    process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
    expect(await usesFinancialCheckout()).toBe(true);
    process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
    expect(await usesFinancialCheckout()).toBe(false);
  });
});
