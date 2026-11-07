import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Rotas do BFF para a segunda oferta do produto (#553, fase 2b). A decisão de
// quem pode ter várias ofertas, e de preço e moeda, é da api: o BFF só checa
// forma, sessão e CSRF e repassa a negação da api sem transformá-la em 200.

vi.mock("server-only", () => ({}));

const {
  createDomainProductOffer,
  setDomainDefaultOffer,
  getSession,
  verifyMutation,
  DomainApiError
} = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    createDomainProductOffer: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    setDomainDefaultOffer: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    getSession: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ identity: { sub: "seller" } })),
    verifyMutation: vi.fn<(...args: unknown[]) => boolean>(() => true),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  createDomainProductOffer,
  setDomainDefaultOffer,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

vi.mock("@/lib/redis-oauth-service", () => ({
  isOAuthStoreUnavailable: () => false
}));

vi.mock("@/lib/mutation-protection.server", () => ({
  CREATE_PRODUCT_OFFER_CSRF_ACTION: "POST:/api/products/offers",
  SET_DEFAULT_OFFER_CSRF_ACTION: "POST:/api/products/offers/default",
  verifyMutation
}));

import { POST as createOffer } from
  "../app/api/sellers/[sellerId]/products/[productId]/offers/route";
import { POST as setDefault } from
  "../app/api/sellers/[sellerId]/products/[productId]/offers/[offerId]/default/route";

const ORIGIN = "http://localhost:3000";
const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const PRODUCT_ID = "55555555-5555-4555-8555-555555555555";
const OFFER_ID = "66666666-6666-4666-8666-666666666666";
const IDEMPOTENCY = "77777777-7777-4777-8777-777777777777";
const OFFERS_PATH = `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/offers`;
const DEFAULT_PATH = `${OFFERS_PATH}/${OFFER_ID}/default`;

const productContext = {
  params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID })
};
const offerContext = {
  params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID, offerId: OFFER_ID })
};
const originalEnvironment = { ...process.env };

const CREATE_FORM = {
  _csrf: "csrf-token-test",
  idempotency_key: IDEMPOTENCY,
  offer_id: OFFER_ID,
  title: "Plano anual",
  summary: "",
  unit_amount_minor: "19700",
  currency: "BRL",
  billing_period: "yearly"
};
const DEFAULT_FORM = {
  _csrf: "csrf-token-test",
  idempotency_key: IDEMPOTENCY,
  expected_offer_version: "3"
};

function formRequest(path: string, entries: Record<string, string>, origin: string | null = ORIGIN) {
  const form = new FormData();
  for (const [key, value] of Object.entries(entries)) form.set(key, value);
  const headers: Record<string, string> = { cookie: "appsell_session=session-1" };
  if (origin !== null) headers.origin = origin;
  return new NextRequest(`${ORIGIN}${path}`, { method: "POST", headers, body: form });
}

describe("rotas da segunda oferta", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createDomainProductOffer.mockResolvedValue({ offer_id: OFFER_ID, offer_version: 1, created: true });
    setDomainDefaultOffer.mockResolvedValue({ offer_id: OFFER_ID, changed: true });
    verifyMutation.mockReturnValue(true);
    getSession.mockResolvedValue({ identity: { sub: "seller" } });
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("repassa preço, moeda e período como vieram, sem decidir nada", async () => {
    // Decisão 005: quem valida limite de preço e moeda é a api. O BFF só
    // converte o número e não troca nem completa nenhum campo.
    const response = await createOffer(formRequest(OFFERS_PATH, CREATE_FORM), productContext);
    expect(response.status).toBe(201);
    expect(createDomainProductOffer).toHaveBeenCalledTimes(1);
    expect(createDomainProductOffer.mock.calls[0]?.slice(2)).toEqual([
      SELLER_ID,
      PRODUCT_ID,
      {
        idempotencyKey: IDEMPOTENCY,
        offerId: OFFER_ID,
        title: "Plano anual",
        summary: "",
        unitAmountMinor: 19700,
        currency: "BRL",
        billingPeriod: "yearly"
      }
    ]);
  });

  it("loja fora da lista: a negação da api chega ao browser nas duas rotas", async () => {
    // A flag é decidida pela api. Sem este repasse, um 200 vazio faria o
    // editor achar que a oferta foi criada.
    const negacao = new DomainApiError(403, "multi_offer_disabled");
    createDomainProductOffer.mockRejectedValueOnce(negacao);
    setDomainDefaultOffer.mockRejectedValueOnce(negacao);

    const criada = await createOffer(formRequest(OFFERS_PATH, CREATE_FORM), productContext);
    const trocada = await setDefault(formRequest(DEFAULT_PATH, DEFAULT_FORM), offerContext);

    for (const response of [criada, trocada]) {
      expect(response.status).toBe(403);
      expect(await response.json()).toEqual({ error: "multi_offer_disabled" });
    }
  });

  it("o browser não liga a flag: campo a mais derruba o pedido antes da api", async () => {
    const comFlag = { ...CREATE_FORM, multi_offer_enabled: "true" };
    const response = await createOffer(formRequest(OFFERS_PATH, comFlag), productContext);
    expect(response.status).toBe(422);
    const padraoComFlag = { ...DEFAULT_FORM, multi_offer_enabled: "true" };
    const outra = await setDefault(formRequest(DEFAULT_PATH, padraoComFlag), offerContext);
    expect(outra.status).toBe(422);
    expect(createDomainProductOffer).not.toHaveBeenCalled();
    expect(setDomainDefaultOffer).not.toHaveBeenCalled();
  });

  it("recusa preço que não é inteiro sem sinal", async () => {
    for (const valor of ["-1", "19.70", "1e3", "", "abc"]) {
      const response = await createOffer(
        formRequest(OFFERS_PATH, { ...CREATE_FORM, unit_amount_minor: valor }),
        productContext
      );
      expect(response.status).toBe(422);
    }
    expect(createDomainProductOffer).not.toHaveBeenCalled();
  });

  it("CSRF, origem e sessão valem como nas outras rotas de oferta", async () => {
    verifyMutation.mockReturnValueOnce(false);
    expect((await createOffer(formRequest(OFFERS_PATH, CREATE_FORM), productContext)).status)
      .toBe(403);
    expect((await setDefault(formRequest(DEFAULT_PATH, DEFAULT_FORM, null), offerContext)).status)
      .toBe(403);
    getSession.mockResolvedValueOnce(null);
    expect((await setDefault(formRequest(DEFAULT_PATH, DEFAULT_FORM), offerContext)).status)
      .toBe(401);
    expect(verifyMutation.mock.calls[0]?.[2]).toBe("POST:/api/products/offers");
    expect(createDomainProductOffer).not.toHaveBeenCalled();
    expect(setDomainDefaultOffer).not.toHaveBeenCalled();
  });

  it("troca da padrão usa o próprio CSRF e repassa a versão esperada", async () => {
    const response = await setDefault(formRequest(DEFAULT_PATH, DEFAULT_FORM), offerContext);
    expect(response.status).toBe(200);
    expect(verifyMutation.mock.calls[0]?.[2]).toBe("POST:/api/products/offers/default");
    expect(setDomainDefaultOffer.mock.calls[0]?.slice(2)).toEqual([
      SELLER_ID,
      PRODUCT_ID,
      OFFER_ID,
      { idempotencyKey: IDEMPOTENCY, expectedOfferVersion: 3 }
    ]);
  });

  it("recusa id de oferta fora do formato antes de chamar a api", async () => {
    const response = await setDefault(
      formRequest(DEFAULT_PATH, DEFAULT_FORM),
      { params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID, offerId: "x" }) }
    );
    expect(response.status).toBe(403);
    expect(setDomainDefaultOffer).not.toHaveBeenCalled();
  });
});
