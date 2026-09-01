import { describe, expect, test, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  IdentityOAuthClient,
  IdentityOAuthConfigurationError,
  parseIdentityOAuthConfig
} from "../lib/identity-oauth-client.server";

const ORIGIN = "https://identity.example";
const STATE = "s".repeat(43);
const CHALLENGE = "c".repeat(43);
const NONCE = "n".repeat(43);

function environment(override: Record<string, string | undefined> = {}) {
  return {
    NODE_ENV: "test",
    APPSELL_PUBLIC_ORIGIN: "https://sell.example",
    APPSELL_IDENTITY_ISSUER: `${ORIGIN}/issuer`,
    APPSELL_IDENTITY_AUTHORIZATION_URL: `${ORIGIN}/authorize`,
    APPSELL_IDENTITY_TOKEN_URL: `${ORIGIN}/token`,
    APPSELL_IDENTITY_JWKS_URL: `${ORIGIN}/jwks`,
    APPSELL_IDENTITY_ALLOWED_ORIGINS: ORIGIN,
    APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: "appsell-web",
    APPSELL_IDENTITY_EXPECTED_AUDIENCE: "cognito-app-client",
    APPSELL_IDENTITY_REDIRECT_URI: "https://sell.example/auth/callback",
    APPSELL_IDENTITY_REFRESH_HANDLE_TTL_SECONDS: "86400",
    ...override
  };
}

function client(override: Record<string, string | undefined> = {}) {
  return new IdentityOAuthClient(parseIdentityOAuthConfig(environment(override)));
}

describe("entrada de cadastro no Identity", () => {
  test("deriva a tela de cadastro ao lado da de autorizacao", () => {
    expect(parseIdentityOAuthConfig(environment()).registrationEndpoint.toString())
      .toBe(`${ORIGIN}/register`);
  });

  test("preserva o prefixo do caminho ao derivar", () => {
    const config = parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_AUTHORIZATION_URL: `${ORIGIN}/oauth/authorize`
    }));
    expect(config.registrationEndpoint.toString()).toBe(`${ORIGIN}/oauth/register`);
  });

  // Producao serve "/login/" com barra final; sem trata-la o caminho derivado
  // viraria "/login/register" e o botao cairia num 404.
  test("descarta a barra final ao derivar", () => {
    const config = parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_AUTHORIZATION_URL: `${ORIGIN}/login/`
    }));
    expect(config.registrationEndpoint.toString()).toBe(`${ORIGIN}/register`);
  });

  test("aceita a tela de cadastro declarada explicitamente", () => {
    const config = parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_REGISTRATION_URL: `${ORIGIN}/criar-conta`
    }));
    expect(config.registrationEndpoint.toString()).toBe(`${ORIGIN}/criar-conta`);
  });

  test("recusa tela de cadastro fora da lista de origens permitidas", () => {
    expect(() => parseIdentityOAuthConfig(environment({
      APPSELL_IDENTITY_REGISTRATION_URL: "https://attacker.example/register"
    }))).toThrow(IdentityOAuthConfigurationError);
  });

  // O Identity le client_id, redirect_uri, state e code_challenge da propria
  // query string. Se a carga divergir da do authorize, quem cria a conta perde
  // o pedido e nunca volta para a loja.
  test("manda para o cadastro a mesma carga do authorize", () => {
    const entrada = { state: STATE, nonce: NONCE, codeChallenge: CHALLENGE };
    const autorizacao = client().authorizationUrl(entrada);
    const cadastro = client().registrationUrl(entrada);

    expect(cadastro.origin).toBe(autorizacao.origin);
    expect(cadastro.pathname).toBe("/register");
    expect(cadastro.search).toBe(autorizacao.search);
    expect(cadastro.searchParams.get("client_id")).toBe("appsell-web");
    expect(cadastro.searchParams.get("redirect_uri")).toBe("https://sell.example/auth/callback");
    expect(cadastro.searchParams.get("state")).toBe(STATE);
    expect(cadastro.searchParams.get("code_challenge")).toBe(CHALLENGE);
    expect(cadastro.searchParams.get("code_challenge_method")).toBe("S256");
  });

  test("recusa desafio PKCE invalido igual ao authorize", () => {
    expect(() => client().registrationUrl({ state: STATE, nonce: NONCE, codeChallenge: "curto" })).toThrow();
  });
});
