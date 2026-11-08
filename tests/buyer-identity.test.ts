import { describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

// A regra de quem pode comprar mora em lib/buyer-identity.server.ts. A página de
// checkout e a rota create-session leem a mesma sessão por ela (#154, achado 2):
// estes testes travam os estados que as duas precisam distinguir.

vi.mock("server-only", () => ({}));

const { getSession } = vi.hoisted(() => ({
  getSession: vi.fn()
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

import { readBuyerSession, resolveBuyerIdentity } from "../lib/buyer-identity.server";

const VERIFIED = { subject: "sub-1", identity: { email: " Buyer@Example.test ", emailVerified: true } };

function request(sessionId?: string): NextRequest {
  const cookies = new Map(sessionId ? [["appsell_session", { value: sessionId }]] : []);
  return { cookies: { get: (name: string) => cookies.get(name) } } as unknown as NextRequest;
}

describe("readBuyerSession", () => {
  it("sem cookie é anônimo, sem perguntar ao session store", async () => {
    expect(await readBuyerSession(undefined)).toEqual({ status: "anonymous" });
    expect(getSession).not.toHaveBeenCalled();
  });

  it("session store fora do ar é indisponível, não anônimo", async () => {
    getSession.mockImplementationOnce(async () => { throw new Error("redis down"); });
    expect(await readBuyerSession("s1")).toEqual({ status: "unavailable" });
  });

  it("sessão vencida e subject de registration: são anônimos", async () => {
    getSession.mockResolvedValueOnce(null);
    expect(await readBuyerSession("s1")).toEqual({ status: "anonymous" });
    getSession.mockResolvedValueOnce({ ...VERIFIED, subject: "registration:buyer@example.test" });
    expect(await readBuyerSession("s1")).toEqual({ status: "anonymous" });
  });

  it("logado sem e-mail verificado é unverified", async () => {
    getSession.mockResolvedValueOnce({ subject: "sub-1", identity: { email: "a@b.test", emailVerified: false } });
    expect(await readBuyerSession("s1")).toEqual({ status: "unverified" });
    getSession.mockResolvedValueOnce({ subject: "sub-1" });
    expect(await readBuyerSession("s1")).toEqual({ status: "unverified" });
  });

  it("logado com e-mail verificado devolve subject e e-mail normalizado", async () => {
    getSession.mockResolvedValueOnce(VERIFIED);
    expect(await readBuyerSession("s1")).toEqual({
      status: "authenticated",
      subject: "sub-1",
      email: "buyer@example.test"
    });
  });
});

describe("resolveBuyerIdentity continua igual", () => {
  it("sem cookie, anônimo", async () => {
    expect(await resolveBuyerIdentity(request(), "buyer@example.test")).toEqual({ status: "anonymous" });
  });

  it("store fora, indisponível", async () => {
    getSession.mockImplementationOnce(async () => { throw new Error("redis down"); });
    expect(await resolveBuyerIdentity(request("s1"), "buyer@example.test")).toEqual({ status: "unavailable" });
  });

  it("e-mail verificado igual ao da compra, autenticado", async () => {
    getSession.mockResolvedValueOnce(VERIFIED);
    expect(await resolveBuyerIdentity(request("s1"), "BUYER@example.test ")).toEqual({
      status: "authenticated",
      subject: "sub-1"
    });
  });

  it("e-mail de outra pessoa ou não verificado, mismatch", async () => {
    getSession.mockResolvedValueOnce(VERIFIED);
    expect(await resolveBuyerIdentity(request("s1"), "outra@example.test")).toEqual({ status: "mismatch" });
    getSession.mockResolvedValueOnce({ subject: "sub-1", identity: { email: "buyer@example.test", emailVerified: false } });
    expect(await resolveBuyerIdentity(request("s1"), "buyer@example.test")).toEqual({ status: "mismatch" });
  });
});
