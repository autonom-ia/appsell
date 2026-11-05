import { randomBytes } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// CSRF de verdade nas rotas da segunda oferta (#553, fase 2b). Condição da
// Plataforma: cada rota aceita só o token da própria ação. Um token válido de
// outra ação, para a mesma sessão, é recusado com 403 antes da api.

vi.mock("server-only", () => ({}));

const { createDomainProductOffer, setDomainDefaultOffer, getSession, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    createDomainProductOffer: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    setDomainDefaultOffer: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    getSession: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ identity: { sub: "seller" } })),
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

import {
  CREATE_PRODUCT_OFFER_CSRF_ACTION,
  csrfTokenForSession,
  SET_DEFAULT_OFFER_CSRF_ACTION,
  TRANSITION_OFFER_STATUS_CSRF_ACTION,
  UPDATE_PRODUCT_DRAFT_CSRF_ACTION
} from "../lib/mutation-protection.server";
import { POST as createOffer } from
  "../app/api/sellers/[sellerId]/products/[productId]/offers/route";
import { POST as setDefault } from
  "../app/api/sellers/[sellerId]/products/[productId]/offers/[offerId]/default/route";

const ORIGIN = "http://localhost:3000";
const SESSION = "session-csrf-offers";
const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const PRODUCT_ID = "55555555-5555-4555-8555-555555555555";
const OFFER_ID = "66666666-6666-4666-8666-666666666666";
const OFFERS_PATH = `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/offers`;
const DEFAULT_PATH = `${OFFERS_PATH}/${OFFER_ID}/default`;
const originalEnvironment = { ...process.env };

const productContext = { params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID }) };
const offerContext = {
  params: Promise.resolve({ sellerId: SELLER_ID, productId: PRODUCT_ID, offerId: OFFER_ID })
};

function createForm(csrf: string) {
  return {
    _csrf: csrf,
    idempotency_key: "77777777-7777-4777-8777-777777777777",
    offer_id: OFFER_ID,
    title: "Plano anual",
    summary: "",
    unit_amount_minor: "19700",
    currency: "BRL",
    billing_period: "yearly"
  };
}

function defaultForm(csrf: string) {
  return {
    _csrf: csrf,
    idempotency_key: "77777777-7777-4777-8777-777777777777",
    expected_offer_version: "2"
  };
}

function formRequest(path: string, entries: Record<string, string>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(entries)) form.set(key, value);
  return new NextRequest(`${ORIGIN}${path}`, {
    method: "POST",
    headers: { cookie: `appsell_session=${SESSION}`, origin: ORIGIN },
    body: form
  });
}

describe("CSRF das rotas da segunda oferta", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    process.env.APPSELL_CSRF_HMAC_KEY_B64 = randomBytes(32).toString("base64");
    createDomainProductOffer.mockResolvedValue({ offer_id: OFFER_ID, offer_version: 1, created: true });
    setDomainDefaultOffer.mockResolvedValue({ offer_id: OFFER_ID, changed: true });
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("as ações novas não repetem nenhuma das existentes", () => {
    expect(CREATE_PRODUCT_OFFER_CSRF_ACTION).toBe("POST:/api/products/offers");
    expect(SET_DEFAULT_OFFER_CSRF_ACTION).toBe("POST:/api/products/offers/default");
    expect(TRANSITION_OFFER_STATUS_CSRF_ACTION).toBe("PATCH:/api/products/offers/status");
  });

  it("criar oferta recusa token de outra ação e aceita o próprio", async () => {
    for (const action of [SET_DEFAULT_OFFER_CSRF_ACTION, TRANSITION_OFFER_STATUS_CSRF_ACTION, UPDATE_PRODUCT_DRAFT_CSRF_ACTION]) {
      const response = await createOffer(
        formRequest(OFFERS_PATH, createForm(csrfTokenForSession(SESSION, action))),
        productContext
      );
      expect(response.status).toBe(403);
    }
    expect(createDomainProductOffer).not.toHaveBeenCalled();

    const ok = await createOffer(
      formRequest(OFFERS_PATH, createForm(csrfTokenForSession(SESSION, CREATE_PRODUCT_OFFER_CSRF_ACTION))),
      productContext
    );
    expect(ok.status).toBe(201);
  });

  it("tornar padrão recusa token de outra ação e aceita o próprio", async () => {
    for (const action of [CREATE_PRODUCT_OFFER_CSRF_ACTION, TRANSITION_OFFER_STATUS_CSRF_ACTION]) {
      const response = await setDefault(
        formRequest(DEFAULT_PATH, defaultForm(csrfTokenForSession(SESSION, action))),
        offerContext
      );
      expect(response.status).toBe(403);
    }
    expect(setDomainDefaultOffer).not.toHaveBeenCalled();

    const ok = await setDefault(
      formRequest(DEFAULT_PATH, defaultForm(csrfTokenForSession(SESSION, SET_DEFAULT_OFFER_CSRF_ACTION))),
      offerContext
    );
    expect(ok.status).toBe(200);
  });

  it("pedido sem _csrf é recusado antes da api nas duas rotas", async () => {
    const semCsrf = (form: Record<string, string>) =>
      Object.fromEntries(Object.entries(form).filter(([name]) => name !== "_csrf"));
    const semCsrfCriar = semCsrf(createForm("x"));
    const semCsrfPadrao = semCsrf(defaultForm("x"));
    const criada = await createOffer(formRequest(OFFERS_PATH, semCsrfCriar), productContext);
    const trocada = await setDefault(formRequest(DEFAULT_PATH, semCsrfPadrao), offerContext);
    expect(criada.status).toBe(422);
    expect(trocada.status).toBe(422);
    expect(createDomainProductOffer).not.toHaveBeenCalled();
    expect(setDomainDefaultOffer).not.toHaveBeenCalled();
  });

  it("token da própria ação de outra sessão também é recusado", async () => {
    const response = await setDefault(
      formRequest(DEFAULT_PATH, defaultForm(csrfTokenForSession("outra-sessao", SET_DEFAULT_OFFER_CSRF_ACTION))),
      offerContext
    );
    expect(response.status).toBe(403);
    expect(setDomainDefaultOffer).not.toHaveBeenCalled();
  });
});
