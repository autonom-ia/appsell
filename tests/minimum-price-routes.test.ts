import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  readDomainProductDraft,
  updateDomainProductDraft,
  updateDomainProductCourseDraft,
  transitionDomainOfferStatus,
  publishDomainProductDraft,
  resolveCheckoutPayoutDestination,
  getSession,
  DomainApiError
} = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    readDomainProductDraft: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    updateDomainProductDraft: vi.fn(async () => ({ ok: true })),
    updateDomainProductCourseDraft: vi.fn(async () => ({ ok: true })),
    transitionDomainOfferStatus: vi.fn(async () => ({ ok: true })),
    publishDomainProductDraft: vi.fn(async () => ({ created: true })),
    resolveCheckoutPayoutDestination: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    getSession: vi.fn(async () => ({ identity: { sub: "seller" } })),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  readDomainProductDraft,
  updateDomainProductDraft,
  updateDomainProductCourseDraft,
  transitionDomainOfferStatus,
  publishDomainProductDraft,
  resolveCheckoutPayoutDestination,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

vi.mock("@/lib/redis-oauth-service", () => ({
  isOAuthStoreUnavailable: () => false
}));

vi.mock("@/lib/mutation-protection.server", () => ({
  CLEAR_PRODUCT_COVER_CSRF_ACTION: "a",
  CONFIRM_COVER_UPLOAD_CSRF_ACTION: "b",
  CREATE_COVER_UPLOAD_CSRF_ACTION: "c",
  PUBLISH_PRODUCT_DRAFT_CSRF_ACTION: "d",
  CREATE_ACCESS_LINK_CSRF_ACTION: "e",
  REVOKE_ACCESS_LINK_CSRF_ACTION: "f",
  ROTATE_ACCESS_LINK_CSRF_ACTION: "g",
  TRANSITION_OFFER_STATUS_CSRF_ACTION: "h",
  UPDATE_PRODUCT_DRAFT_CSRF_ACTION: "i",
  UPDATE_PRODUCT_HEADLINE_CSRF_ACTION: "j",
  csrfTokenForSession: () => "csrf-token-test",
  verifyMutation: () => true
}));

import { PATCH as patchProduct } from "../app/api/sellers/[sellerId]/products/[productId]/route";
import { PATCH as patchOfferStatus } from
  "../app/api/sellers/[sellerId]/products/[productId]/offers/[offerId]/status/route";
import { POST as publish } from "../app/api/sellers/[sellerId]/products/[productId]/publications/route";

const ORIGIN = "http://localhost:3000";
const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const PRODUCT_ID = "55555555-5555-4555-8555-555555555555";
const OFFER_ID = "66666666-6666-4666-8666-666666666666";
const COURSE_ID = "88888888-8888-4888-8888-888888888888";
const IDEMPOTENCY = "77777777-7777-4777-8777-777777777777";
const originalEnvironment = { ...process.env };

const productContext = { params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID }) };
const offerContext = {
  params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID, offerId: OFFER_ID })
};

function formRequest(method: string, path: string, entries: Record<string, string>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(entries)) form.set(key, value);
  return new NextRequest(`${ORIGIN}${path}`, {
    method,
    headers: { cookie: "appsell_session=session-1", origin: ORIGIN },
    body: form
  });
}

function priceUpdate(amount: string, currency = "BRL") {
  return formRequest("PATCH", `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, {
    _csrf: "csrf-token-test",
    idempotency_key: IDEMPOTENCY,
    course_id: COURSE_ID,
    offer_id: OFFER_ID,
    expected_product_version: "1",
    expected_course_version: "1",
    expected_offer_version: "1",
    slug: "curso-teste",
    product_title: "Curso teste",
    product_description: "Descrição",
    course_title: "Curso teste",
    offer_title: "Oferta",
    offer_summary: "",
    offer_unit_amount_minor: amount,
    offer_currency: currency,
    offer_billing_period: "one_time"
  });
}

function activate() {
  return formRequest("PATCH", `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/offers/${OFFER_ID}/status`, {
    _csrf: "csrf-token-test",
    idempotency_key: IDEMPOTENCY,
    expected_status: "draft",
    status: "active",
    expected_offer_version: "1"
  });
}

function publishRequest(accessMode: "free" | "paid") {
  return formRequest("POST", `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/publications`, {
    _csrf: "csrf-token-test",
    idempotency_key: IDEMPOTENCY,
    course_id: COURSE_ID,
    offer_id: OFFER_ID,
    expected_product_version: "1",
    expected_course_version: "1",
    expected_offer_version: "1",
    access_mode: accessMode
  });
}

function draftWithPrice(amount: number | null, currency = "BRL", offerId = OFFER_ID) {
  return {
    product: {
      id: PRODUCT_ID,
      offer: { id: offerId, status: "draft", unit_amount_minor: amount, currency, billing_period: "one_time" }
    }
  };
}

const BELOW_MINIMUM = {
  error: "invalid_product_input",
  field: "offer_unit_amount_minor",
  reason: "below_minimum",
  minimum_minor: 52
};

describe("preço mínimo nas rotas do produtor (#546)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSession.mockResolvedValue({ identity: { sub: "seller" } });
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    delete process.env.APPSELL_CHECKOUT_PROVIDER;
    delete process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS;
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  describe("mudar o preço", () => {
    it("recusa preço igual à comissão e diz o mínimo, sem chamar a api", async () => {
      // Pega: preço em que a loja recebe zero gravado como se fosse vendável.
      const response = await patchProduct(priceUpdate("51"), productContext);
      expect(response.status).toBe(422);
      expect(await response.json()).toEqual(BELOW_MINIMUM);
      expect(updateDomainProductDraft).not.toHaveBeenCalled();
    });

    it("aceita o primeiro preço em que a loja recebe algo", async () => {
      const response = await patchProduct(priceUpdate("52"), productContext);
      expect(response.status).toBe(200);
      expect(updateDomainProductDraft).toHaveBeenCalledTimes(1);
    });

    it("continua aceitando preço zero no rascunho", async () => {
      // Pega: a trava quebrando o rascunho sem preço, que já era permitido.
      const response = await patchProduct(priceUpdate("0"), productContext);
      expect(response.status).toBe(200);
    });

    it("não aplica a regra do real a outra moeda", async () => {
      const response = await patchProduct(priceUpdate("10", "USD"), productContext);
      expect(response.status).toBe(200);
    });
  });

  describe("ativar vendas", () => {
    it("recusa ativar oferta com preço abaixo do mínimo", async () => {
      // Pega: preço antigo, gravado antes da trava, virando venda ativa.
      readDomainProductDraft.mockResolvedValue(draftWithPrice(51));
      const response = await patchOfferStatus(activate(), offerContext);
      expect(response.status).toBe(422);
      expect(await response.json()).toEqual(BELOW_MINIMUM);
      expect(transitionDomainOfferStatus).not.toHaveBeenCalled();
    });

    it("ativa oferta com preço acima do mínimo", async () => {
      readDomainProductDraft.mockResolvedValue(draftWithPrice(4_700));
      const response = await patchOfferStatus(activate(), offerContext);
      expect(response.status).toBe(200);
      expect(transitionDomainOfferStatus).toHaveBeenCalledTimes(1);
    });

    it("falha fechado quando o rascunho vem de outra oferta", async () => {
      // Pega: conferir o preço de uma oferta e ativar outra.
      readDomainProductDraft.mockResolvedValue(draftWithPrice(4_700, "BRL", COURSE_ID));
      const response = await patchOfferStatus(activate(), offerContext);
      expect(response.status).toBe(503);
      expect(transitionDomainOfferStatus).not.toHaveBeenCalled();
    });

    it("falha fechado quando não consegue ler o rascunho", async () => {
      readDomainProductDraft.mockRejectedValue(new DomainApiError(503, "domain_unavailable"));
      const response = await patchOfferStatus(activate(), offerContext);
      expect(response.status).toBe(503);
      expect(transitionDomainOfferStatus).not.toHaveBeenCalled();
    });

    it("não lê o rascunho para arquivar", async () => {
      // Pega: a trava impedindo a loja de tirar do ar uma oferta barata.
      const request = formRequest(
        "PATCH",
        `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/offers/${OFFER_ID}/status`,
        {
          _csrf: "csrf-token-test",
          idempotency_key: IDEMPOTENCY,
          expected_status: "active",
          status: "archived",
          expected_offer_version: "1"
        }
      );
      const response = await patchOfferStatus(request, offerContext);
      expect(response.status).toBe(200);
      expect(readDomainProductDraft).not.toHaveBeenCalled();
    });
  });

  describe("publicar", () => {
    it("recusa publicar pago com preço abaixo do mínimo", async () => {
      readDomainProductDraft.mockResolvedValue(draftWithPrice(51));
      const response = await publish(publishRequest("paid"), productContext);
      expect(response.status).toBe(422);
      expect(await response.json()).toEqual(BELOW_MINIMUM);
      expect(publishDomainProductDraft).not.toHaveBeenCalled();
    });

    it("publica pago com preço acima do mínimo", async () => {
      readDomainProductDraft.mockResolvedValue(draftWithPrice(8_700));
      const response = await publish(publishRequest("paid"), productContext);
      expect(response.status).toBe(201);
    });

    it("publica grátis sem olhar o preço", async () => {
      // Pega: a trava bloqueando produto gratuito.
      const response = await publish(publishRequest("free"), productContext);
      expect(response.status).toBe(201);
      expect(readDomainProductDraft).not.toHaveBeenCalled();
    });
  });

  describe("escopo: só a loja com comissão", () => {
    // Sem comissão a loja recebe o valor inteiro; a trava não tem o que
    // proteger e não pode alcançar a Hub2You (AGENTS.md §7).
    const READY = { payout_destination: { provider: "stripe", provider_account_id: "acct_a", charges_enabled: true } };

    function financial(listed: boolean) {
      process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
      process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = listed ? SELLER_ID : "";
    }

    it("Financial fora da lista Connect grava, ativa e publica a 51 centavos", async () => {
      // Pega: a trava recusando preço de loja que não paga comissão.
      financial(false);
      readDomainProductDraft.mockResolvedValue(draftWithPrice(51));
      expect((await patchProduct(priceUpdate("51"), productContext)).status).toBe(200);
      expect(updateDomainProductDraft).toHaveBeenCalledTimes(1);
      expect((await patchOfferStatus(activate(), offerContext)).status).toBe(200);
      expect(transitionDomainOfferStatus).toHaveBeenCalledTimes(1);
      expect((await publish(publishRequest("paid"), productContext)).status).toBe(201);
      expect(resolveCheckoutPayoutDestination).not.toHaveBeenCalled();
    });

    it("Financial na lista Connect recusa 51 centavos nas três rotas", async () => {
      financial(true);
      readDomainProductDraft.mockResolvedValue(draftWithPrice(51));
      const responses = [
        await patchProduct(priceUpdate("51"), productContext),
        await patchOfferStatus(activate(), offerContext),
        await publish(publishRequest("paid"), productContext)
      ];
      for (const response of responses) {
        expect(response.status).toBe(422);
        expect(await response.json()).toEqual(BELOW_MINIMUM);
      }
      expect(updateDomainProductDraft).not.toHaveBeenCalled();
      expect(transitionDomainOfferStatus).not.toHaveBeenCalled();
      expect(publishDomainProductDraft).not.toHaveBeenCalled();
    });

    it("local_stripe com conta de repasse recusa; sem conta, grava", async () => {
      process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
      resolveCheckoutPayoutDestination.mockResolvedValueOnce(READY);
      expect((await patchProduct(priceUpdate("51"), productContext)).status).toBe(422);
      expect(resolveCheckoutPayoutDestination).toHaveBeenCalledWith(SELLER_ID);
      resolveCheckoutPayoutDestination.mockResolvedValueOnce({ payout_destination: null });
      expect((await patchProduct(priceUpdate("51"), productContext)).status).toBe(200);
      expect(updateDomainProductDraft).toHaveBeenCalledTimes(1);
    });

    it("falha fechado quando não consegue ler a conta de repasse", async () => {
      process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
      resolveCheckoutPayoutDestination.mockRejectedValue(new DomainApiError(503, "domain_unavailable"));
      const response = await patchProduct(priceUpdate("51"), productContext);
      expect(response.status).toBe(503);
      expect(updateDomainProductDraft).not.toHaveBeenCalled();
    });

    it("não diz nada da comissão de uma loja que não é de quem pede", async () => {
      // Pega: o 422 revelando se outra loja está na lista ou tem conta.
      financial(true);
      readDomainProductDraft.mockRejectedValue(new DomainApiError(403, "forbidden"));
      const response = await patchProduct(priceUpdate("51"), productContext);
      expect(response.status).toBe(403);
      expect(updateDomainProductDraft).not.toHaveBeenCalled();
    });

    it("preço acima do mínimo não consulta comissão nem lê o rascunho", async () => {
      process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
      expect((await patchProduct(priceUpdate("4700"), productContext)).status).toBe(200);
      expect(readDomainProductDraft).not.toHaveBeenCalled();
      expect(resolveCheckoutPayoutDestination).not.toHaveBeenCalled();
    });
  });
});
