import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  recordDomainEmailConsent,
  revokeDomainEmailConsent,
  authenticatedSession,
  verifyMutation,
  consumeRateLimit,
  DomainApiError
} = vi.hoisted(() => {
    class DomainApiError extends Error {
      constructor(readonly status: number, readonly code: string) {
        super(code);
      }
    }
    return {
      recordDomainEmailConsent: vi.fn(),
      revokeDomainEmailConsent: vi.fn(),
      authenticatedSession: vi.fn(),
      verifyMutation: vi.fn(),
      consumeRateLimit: vi.fn(),
      DomainApiError
    };
  });

vi.mock("@/lib/internal-api-client.server", () => ({
  recordDomainEmailConsent,
  revokeDomainEmailConsent,
  DomainApiError
}));
vi.mock("@/lib/mutation-protection.server", () => ({ verifyMutation }));
vi.mock("@/lib/oauth-service.server", () => ({ oauthService: () => ({ consumeRateLimit }) }));
vi.mock("@/app/api/sellers/[sellerId]/products/[productId]/_route-shared", () => ({
  authenticatedSession,
  canonicalUuid: (value: string | undefined) =>
    value !== undefined && value.length === 36 && value.split("-").length === 5 ? value : null,
  mappedError: (error: unknown) =>
    new Response(null, { status: error instanceof DomainApiError ? error.status : 503 })
}));

import { POST } from "../app/api/email/consent/route";
import { ACAO_CSRF_DO_CONSENTIMENTO } from "../lib/email-consent/texto";
import {
  gravarConsentimentoDeEmail,
  revogarConsentimentoDeEmail
} from "../lib/email-consent/caixa-de-consentimento";

const LOJA = "44444444-4444-4444-8444-444444444444";

function pedido(campos: Record<string, string>) {
  return new NextRequest("https://sell.test/api/email/consent", {
    method: "POST",
    body: new URLSearchParams(campos),
    headers: { "content-type": "application/x-www-form-urlencoded" }
  });
}

describe("gravação do consentimento de e-mail da loja", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("APPSELL_LEADS_PILOT_SELLER_IDS", LOJA);
    authenticatedSession.mockResolvedValue({ sessionId: "s", session: { subject: "u" } });
    verifyMutation.mockReturnValue(true);
    recordDomainEmailConsent.mockResolvedValue(undefined);
    revokeDomainEmailConsent.mockResolvedValue(undefined);
    consumeRateLimit.mockResolvedValue(true);
  });

  it("grava com a origem e responde 204", async () => {
    const resposta = await POST(pedido({ seller: LOJA, decision: "grant", source: "checkout", _csrf: "t" }));
    expect(resposta.status).toBe(204);
    expect(verifyMutation).toHaveBeenCalledWith(expect.anything(), "s", ACAO_CSRF_DO_CONSENTIMENTO, "t");
    expect(recordDomainEmailConsent).toHaveBeenCalledWith("s", { subject: "u" }, LOJA, "checkout");
  });

  it("sem sessão responde 401 e sem token válido responde 403, sem gravar", async () => {
    authenticatedSession.mockResolvedValueOnce(null);
    expect((await POST(pedido({ seller: LOJA, decision: "grant", source: "account", _csrf: "t" }))).status).toBe(401);
    verifyMutation.mockReturnValueOnce(false);
    expect((await POST(pedido({ seller: LOJA, decision: "grant", source: "account", _csrf: "x" }))).status).toBe(403);
    expect(recordDomainEmailConsent).not.toHaveBeenCalled();
  });

  it("loja ou origem inválida responde 422, e fora do piloto 404", async () => {
    expect((await POST(pedido({ seller: "x", decision: "grant", source: "account", _csrf: "t" }))).status).toBe(422);
    expect((await POST(pedido({ seller: LOJA, decision: "grant", source: "storefront_form", _csrf: "t" }))).status).toBe(422);
    vi.stubEnv("APPSELL_LEADS_PILOT_SELLER_IDS", "");
    expect((await POST(pedido({ seller: LOJA, decision: "grant", source: "account", _csrf: "t" }))).status).toBe(404);
    expect(recordDomainEmailConsent).not.toHaveBeenCalled();
  });

  it("o e-mail nunca vai no pedido do navegador", async () => {
    const enviar = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const gravou = await gravarConsentimentoDeEmail({ sellerId: LOJA, csrf: "t", origem: "account" }, enviar);
    expect(gravou).toBe(true);
    const [, opcoes] = enviar.mock.calls[0]!;
    const corpo = new URLSearchParams(String(opcoes.body));
    expect([...corpo.keys()].sort()).toEqual(["_csrf", "decision", "seller", "source"]);
    expect(corpo.get("decision")).toBe("grant");
  });

  it("revoga sem origem, pela mesma ação anti-CSRF", async () => {
    const resposta = await POST(pedido({ seller: LOJA, decision: "revoke", _csrf: "t" }));
    expect(resposta.status).toBe(204);
    expect(revokeDomainEmailConsent).toHaveBeenCalledWith("s", { subject: "u" }, LOJA);
    expect(recordDomainEmailConsent).not.toHaveBeenCalled();
    const enviar = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await revogarConsentimentoDeEmail({ sellerId: LOJA, csrf: "t" }, enviar)).toBe(true);
    const corpo = new URLSearchParams(String(enviar.mock.calls[0]![1].body));
    expect([...corpo.keys()].sort()).toEqual(["_csrf", "decision", "seller"]);
  });

  it("decisão ausente, desconhecida ou revogação com origem responde 422", async () => {
    const casos: Record<string, string>[] = [
      { seller: LOJA, source: "account", _csrf: "t" },
      { seller: LOJA, decision: "talvez", source: "account", _csrf: "t" },
      { seller: LOJA, decision: "revoke", source: "account", _csrf: "t" },
      { seller: LOJA, decision: "grant", _csrf: "t" }
    ];
    for (const campos of casos) {
      expect((await POST(pedido(campos))).status).toBe(422);
    }
    expect(recordDomainEmailConsent).not.toHaveBeenCalled();
    expect(revokeDomainEmailConsent).not.toHaveBeenCalled();
  });

  it("limite por pessoa: acima dele responde 429 sem gravar", async () => {
    consumeRateLimit.mockResolvedValueOnce(false);
    const resposta = await POST(pedido({ seller: LOJA, decision: "grant", source: "account", _csrf: "t" }));
    expect(resposta.status).toBe(429);
    expect(consumeRateLimit).toHaveBeenCalledWith("email-consent", "u", expect.objectContaining({ maxPerClient: 10 }));
    expect(recordDomainEmailConsent).not.toHaveBeenCalled();
  });

  it("falha de rede não lança: devolve false", async () => {
    const enviar = vi.fn().mockRejectedValue(new Error("rede"));
    expect(await gravarConsentimentoDeEmail({ sellerId: LOJA, csrf: "t", origem: "checkout" }, enviar)).toBe(false);
  });
});
