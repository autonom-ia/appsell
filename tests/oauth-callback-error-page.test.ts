import { describe, expect, it, vi } from "vitest";

// O adapter do Identity é server-only; no vitest o marcador não existe.
vi.mock("server-only", () => ({}));

import { IdentityOAuthEmailUnverifiedError, IdentityOAuthProtocolError } from "../lib/identity-oauth-client.server";
import { messagesForOutcome, renderCallbackErrorPage } from "../lib/oauth-callback-error-page";
import { describeCallbackFailure } from "../lib/oauth-callback-telemetry";

// #301: a pessoa com e-mail não confirmado caía numa tela cujo único botão era
// "Entrar novamente" — e entrar de novo produz a mesma recusa. Só esse caso
// ganha texto próprio; todo outro desfecho continua com a mensagem neutra,
// porque a página também serve falhas de configuração e não pode virar oráculo.

describe("página de erro do callback", () => {
  it("diz o caminho de saída quando o e-mail não foi confirmado", () => {
    const html = renderCallbackErrorPage("identity_email_unverified");
    expect(html).toContain("Confirme seu e-mail");
    expect(html).toContain("clique no link de confirmação e depois entre de novo");
    expect(html).toContain("Já confirmei, entrar");
    expect(html).not.toContain("Entrar novamente");
    expect(html).toContain("<!-- identity_email_unverified -->");
  });

  it("mantém a mensagem neutra e idêntica para todos os outros desfechos", () => {
    const outros = ["invalid_oauth_callback", "identity_not_configured", "identity_unavailable", "oauth_store_unavailable", "x"];
    const corpos = outros.map((outcome) => renderCallbackErrorPage(outcome).replace(/<!-- [^>]* -->/, ""));
    expect(new Set(corpos).size).toBe(1);
    expect(corpos[0]).toContain("Não foi possível concluir o acesso");
    expect(corpos[0]).not.toContain("Confirme seu e-mail");
    expect(messagesForOutcome("identity_not_configured")).toBe(messagesForOutcome("x"));
  });

  it("o erro de e-mail não verificado continua sendo um erro de protocolo para a telemetria", () => {
    const erro = new IdentityOAuthEmailUnverifiedError("Identity email is not verified.");
    expect(erro).toBeInstanceOf(IdentityOAuthProtocolError);
    expect(describeCallbackFailure(erro)).toBe("Identity email is not verified.");
  });
});
