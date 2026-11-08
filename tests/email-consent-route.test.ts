import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const { recordDomainEmailConsent, authenticatedSession, verifyMutation, DomainApiError } =
  vi.hoisted(() => {
    class DomainApiError extends Error {
      constructor(readonly status: number, readonly code: string) {
        super(code);
      }
    }
    return {
      recordDomainEmailConsent: vi.fn(),
      authenticatedSession: vi.fn(),
      verifyMutation: vi.fn(),
      DomainApiError
    };
  });

vi.mock("@/lib/internal-api-client.server", () => ({ recordDomainEmailConsent, DomainApiError }));
vi.mock("@/lib/mutation-protection.server", () => ({ verifyMutation }));
vi.mock("@/app/api/sellers/[sellerId]/products/[productId]/_route-shared", () => ({
  authenticatedSession,
  canonicalUuid: (value: string | undefined) =>
    value !== undefined && value.length === 36 && value.split("-").length === 5 ? value : null,
  mappedError: (error: unknown) =>
    new Response(null, { status: error instanceof DomainApiError ? error.status : 503 })
}));

import { POST } from "../app/api/email/consent/route";
import { ACAO_CSRF_DO_CONSENTIMENTO } from "../lib/email-consent/texto";
import { gravarConsentimentoDeEmail } from "../lib/email-consent/caixa-de-consentimento";

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
  });

  it("grava com a origem e responde 204", async () => {
    const resposta = await POST(pedido({ seller: LOJA, source: "checkout", _csrf: "t" }));
    expect(resposta.status).toBe(204);
    expect(verifyMutation).toHaveBeenCalledWith(expect.anything(), "s", ACAO_CSRF_DO_CONSENTIMENTO, "t");
    expect(recordDomainEmailConsent).toHaveBeenCalledWith("s", { subject: "u" }, LOJA, "checkout");
  });

  it("sem sessão responde 401 e sem token válido responde 403, sem gravar", async () => {
    authenticatedSession.mockResolvedValueOnce(null);
    expect((await POST(pedido({ seller: LOJA, source: "account", _csrf: "t" }))).status).toBe(401);
    verifyMutation.mockReturnValueOnce(false);
    expect((await POST(pedido({ seller: LOJA, source: "account", _csrf: "x" }))).status).toBe(403);
    expect(recordDomainEmailConsent).not.toHaveBeenCalled();
  });

  it("loja ou origem inválida responde 422, e fora do piloto 404", async () => {
    expect((await POST(pedido({ seller: "x", source: "account", _csrf: "t" }))).status).toBe(422);
    expect((await POST(pedido({ seller: LOJA, source: "storefront_form", _csrf: "t" }))).status).toBe(422);
    vi.stubEnv("APPSELL_LEADS_PILOT_SELLER_IDS", "");
    expect((await POST(pedido({ seller: LOJA, source: "account", _csrf: "t" }))).status).toBe(404);
    expect(recordDomainEmailConsent).not.toHaveBeenCalled();
  });

  it("o e-mail nunca vai no pedido do navegador", async () => {
    const enviar = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const gravou = await gravarConsentimentoDeEmail({ sellerId: LOJA, csrf: "t", origem: "account" }, enviar);
    expect(gravou).toBe(true);
    const [, opcoes] = enviar.mock.calls[0]!;
    const corpo = new URLSearchParams(String(opcoes.body));
    expect([...corpo.keys()].sort()).toEqual(["_csrf", "seller", "source"]);
  });

  it("falha de rede não lança: devolve false", async () => {
    const enviar = vi.fn().mockRejectedValue(new Error("rede"));
    expect(await gravarConsentimentoDeEmail({ sellerId: LOJA, csrf: "t", origem: "checkout" }, enviar)).toBe(false);
  });
});
