import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const { chamarRastreio, authenticatedSession, listDomainCampaignLinks, createDomainCampaignLink, verifyMutation } =
  vi.hoisted(() => ({
    chamarRastreio: vi.fn(),
    authenticatedSession: vi.fn(),
    listDomainCampaignLinks: vi.fn(),
    createDomainCampaignLink: vi.fn(),
    verifyMutation: vi.fn()
  }));

vi.mock("@/lib/tracking/servico.server", async (original) => {
  const real = await original<typeof import("@/lib/tracking/servico.server")>();
  return { ...real, chamarRastreio, registrarFalha: vi.fn() };
});
vi.mock("@/lib/internal-api-client.server", () => ({
  listDomainCampaignLinks,
  createDomainCampaignLink,
  archiveDomainCampaignLink: vi.fn(),
  readDomainCampaignSales: vi.fn(),
  DomainApiError: class extends Error {}
}));
vi.mock("@/lib/mutation-protection.server", () => ({
  verifyMutation,
  csrfTokenForSession: () => "token-csrf"
}));
vi.mock("@/app/api/sellers/[sellerId]/products/[productId]/_route-shared", () => ({
  authenticatedSession,
  canonicalUuid: (value: string | undefined) =>
    value !== undefined && value.length === 36 && value.split("-").length === 5 ? value : null,
  mappedError: () => new Response(null, { status: 503 })
}));

import { POST as consentimento } from "../app/api/tracking/consent/route";
import { GET as dados } from "../app/(protected)/dashboard/links/dados/route";
import { POST as criar } from "../app/(protected)/dashboard/links/criar/route";
import { cookieDeAtribuicao, serializarAtribuicao } from "@/lib/tracking/attribution";
import { registrarAtribuicaoDoCheckout } from "@/lib/tracking/checkout-attribution.server";
import { cookieDeEscolha, serializarEscolha } from "@/lib/tracking/consent";
import { limiteDoConsentimento } from "@/lib/tracking/consent-limit";
import { urlDoLink } from "@/lib/tracking/link-url";
import { periodoValido } from "@/lib/tracking/period";

const LOJA = "44444444-4444-4444-8444-444444444444";
const LINK = "55555555-5555-4555-8555-555555555555";
const AGORA = 1_793_000_000;

function cookies(valores: Record<string, string>) {
  return { cookies: { get: (nome: string) => (nome in valores ? { value: valores[nome]! } : undefined) } };
}

function pedidoDeConsentimento(corpo: Record<string, string>, cabecalhos: Record<string, string> = {}) {
  return new NextRequest("https://sell.test/api/tracking/consent", {
    method: "POST",
    body: new URLSearchParams(corpo),
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      "sec-fetch-site": "same-origin",
      "x-forwarded-for": "203.0.113.9",
      ...cabecalhos
    }
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", LOJA);
  limiteDoConsentimento.limparParaTeste();
  chamarRastreio.mockResolvedValue(undefined);
  authenticatedSession.mockResolvedValue({ sessionId: "s", session: { subject: "u" } });
});

describe("URL do link", () => {
  it("vitrine ou página do produto, com UTM e o id do link", () => {
    const base = { id: LINK, utm_source: "insta", utm_medium: null, utm_campaign: "out", utm_content: null, utm_term: null };
    expect(urlDoLink("https://sell.test", "hub2you", { ...base, destination_page_slug: null })).toBe(
      `https://sell.test/s/hub2you?utm_source=insta&utm_campaign=out&sl=${LINK}`
    );
    expect(urlDoLink("https://sell.test", "hub2you", { ...base, destination_page_slug: "curso" })).toBe(
      `https://sell.test/s/hub2you/p/curso?utm_source=insta&utm_campaign=out&sl=${LINK}`
    );
  });

  it("período de até 366 dias de calendário", () => {
    expect(periodoValido("2025-10-01", "2026-10-01")).not.toBeNull();
    expect(periodoValido("2025-10-01", "2026-10-02")).toBeNull();
  });
});

describe("atribuição no checkout", () => {
  const atribuicao = serializarAtribuicao({ utm: { utm_source: "insta" }, link: LINK, visitadoEm: AGORA });
  const aceito = serializarEscolha("accepted");

  it("com aceite e cookie válido, manda a referência da compra à API", async () => {
    await registrarAtribuicaoDoCheckout(cookies({ [cookieDeAtribuicao(LOJA)]: atribuicao, [cookieDeEscolha(LOJA)]: aceito }), LOJA, "intent-1", AGORA);
    expect(chamarRastreio).toHaveBeenCalledWith("/v1/public/tracking/checkout-attribution", {
      seller_organization_id: LOJA,
      purchase_reference: "intent-1",
      link_id: LINK,
      utm_source: "insta",
      utm_medium: null,
      utm_campaign: null,
      utm_content: null,
      utm_term: null,
      visited_at: AGORA,
      consent_text_version: "v1"
    });
  });

  it("sem aceite ou fora do piloto, não chama nada", async () => {
    await registrarAtribuicaoDoCheckout(cookies({ [cookieDeAtribuicao(LOJA)]: atribuicao }), LOJA, "intent-1", AGORA);
    vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", "");
    await registrarAtribuicaoDoCheckout(cookies({ [cookieDeAtribuicao(LOJA)]: atribuicao, [cookieDeEscolha(LOJA)]: aceito }), LOJA, "intent-1", AGORA);
    expect(chamarRastreio).not.toHaveBeenCalled();
  });

  it("nunca lança erro, nem com a API fora do ar nem com cookie que explode", async () => {
    chamarRastreio.mockRejectedValue(new Error("tracking_unavailable"));
    await expect(
      registrarAtribuicaoDoCheckout(cookies({ [cookieDeAtribuicao(LOJA)]: atribuicao, [cookieDeEscolha(LOJA)]: aceito }), LOJA, "intent-1", AGORA)
    ).resolves.toBeUndefined();
    const quebrado = { cookies: { get: () => { throw new Error("boom"); } } };
    await expect(registrarAtribuicaoDoCheckout(quebrado, LOJA, "intent-1", AGORA)).resolves.toBeUndefined();
  });
});

describe("rota de consentimento do banner", () => {
  it("conta o aceite da loja no piloto, sem dado pessoal", async () => {
    const resposta = await consentimento(pedidoDeConsentimento({ seller: LOJA, choice: serializarEscolha("accepted") }));
    expect(resposta.status).toBe(204);
    expect(chamarRastreio).toHaveBeenCalledWith("/v1/public/tracking/cookie-consent", {
      seller_organization_id: LOJA,
      text_version: "v1",
      accepted: true
    });
  });

  it("recusa pedido de outro site, corpo inválido e loja fora do piloto não conta", async () => {
    expect((await consentimento(pedidoDeConsentimento({ seller: LOJA, choice: "v1.accepted" }, { "sec-fetch-site": "cross-site" }))).status).toBe(403);
    expect((await consentimento(pedidoDeConsentimento({ seller: "x", choice: "v1.accepted" }))).status).toBe(422);
    expect((await consentimento(pedidoDeConsentimento({ seller: LOJA, choice: "v1.talvez" }))).status).toBe(422);
    vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", "");
    expect((await consentimento(pedidoDeConsentimento({ seller: LOJA, choice: "v1.accepted" }))).status).toBe(204);
    expect(chamarRastreio).not.toHaveBeenCalled();
  });

  it("o IP do limite é o que a borda viu (último item), não o que o cliente escreveu", async () => {
    for (let i = 0; i < 20; i += 1) {
      const resposta = await consentimento(
        pedidoDeConsentimento({ seller: LOJA, choice: "v1.refused" }, { "x-forwarded-for": `10.0.0.${i}, 203.0.113.9` })
      );
      expect(resposta.status).toBe(204);
    }
    const trocandoOPrimeiro = await consentimento(
      pedidoDeConsentimento({ seller: LOJA, choice: "v1.refused" }, { "x-forwarded-for": "10.9.9.9, 203.0.113.9" })
    );
    expect(trocandoOPrimeiro.status).toBe(429);
  });

  it("limita 20 por minuto por IP", async () => {
    for (let i = 0; i < 20; i += 1) {
      expect((await consentimento(pedidoDeConsentimento({ seller: LOJA, choice: "v1.refused" }))).status).toBe(204);
    }
    expect((await consentimento(pedidoDeConsentimento({ seller: LOJA, choice: "v1.refused" }))).status).toBe(429);
  });

  it("API fora do ar vira 503", async () => {
    chamarRastreio.mockRejectedValue(new Error("tracking_unavailable"));
    expect((await consentimento(pedidoDeConsentimento({ seller: LOJA, choice: "v1.refused" }))).status).toBe(503);
  });
});

describe("rotas do painel de links", () => {
  it("fora do piloto responde 404 e não chama a API", async () => {
    vi.stubEnv("APPSELL_UTM_PILOT_SELLER_IDS", "");
    const resposta = await dados(new NextRequest(`https://sell.test/dashboard/links/dados?seller=${LOJA}`));
    expect(resposta.status).toBe(404);
    expect(listDomainCampaignLinks).not.toHaveBeenCalled();
  });

  it("sem sessão responde 401; com sessão devolve links e o token", async () => {
    authenticatedSession.mockResolvedValueOnce(null);
    expect((await dados(new NextRequest(`https://sell.test/dashboard/links/dados?seller=${LOJA}`))).status).toBe(401);
    listDomainCampaignLinks.mockResolvedValue({ links: [{ id: LINK }] });
    const resposta = await dados(new NextRequest(`https://sell.test/dashboard/links/dados?seller=${LOJA}`));
    expect(await resposta.json()).toEqual({ links: [{ id: LINK }], csrf: "token-csrf" });
  });

  function pedidoDeCriar(campos: Record<string, string>) {
    return new NextRequest("https://sell.test/dashboard/links/criar", {
      method: "POST",
      body: new URLSearchParams(campos),
      headers: { "content-type": "application/x-www-form-urlencoded" }
    });
  }

  it("criar sem CSRF válido responde 403 e não chama a API", async () => {
    verifyMutation.mockReturnValue(false);
    const resposta = await criar(pedidoDeCriar({ seller: LOJA, name: "x", utm_source: "insta", _csrf: "ruim" }));
    expect(resposta.status).toBe(403);
    expect(createDomainCampaignLink).not.toHaveBeenCalled();
  });

  it("criar com CSRF manda os campos aparados, e vazio vira null", async () => {
    verifyMutation.mockReturnValue(true);
    createDomainCampaignLink.mockResolvedValue({ id: LINK });
    const resposta = await criar(
      pedidoDeCriar({ seller: LOJA, name: " Insta ", utm_source: "insta", utm_campaign: "", _csrf: "ok" })
    );
    expect(resposta.status).toBe(201);
    expect(createDomainCampaignLink).toHaveBeenCalledWith("s", { subject: "u" }, LOJA, {
      name: "Insta",
      destinationPageSlug: null,
      utmSource: "insta",
      utmMedium: null,
      utmCampaign: null,
      utmContent: null,
      utmTerm: null
    });
  });

  it("criar sem origem responde 422", async () => {
    verifyMutation.mockReturnValue(true);
    expect((await criar(pedidoDeCriar({ seller: LOJA, name: "x", _csrf: "ok" }))).status).toBe(422);
  });
});
