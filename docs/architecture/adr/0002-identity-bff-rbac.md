# ADR 0002 — Identity, BFF, sessão opaca e RBAC

- Status: Accepted with gates
- Data: 2026-07-10
- Decisores: Rodrigo / Identity / AppSec / Arquitetura
- Issue: [autonom-ia/appsell#9](https://github.com/autonom-ia/appsell/issues/9)

## Contexto

O Identity suporta OAuth Authorization Code + PKCE, mas os SDKs observados podem
persistir tokens no browser. Também há riscos confirmados nos serviços internos:
tokens não podem ser apenas decodificados, e-mail não pode elevar privilégio e o
Admin atual não oferece RBAC seguro para sellers/buyers.

## Decisão

O AppSell usa Identity como provedor OAuth, mas possui sessão própria no BFF:

1. Browser inicia `/auth/login` no mesmo host.
2. BFF gera `state`, nonce e PKCE verifier criptograficamente aleatórios.
3. Material transitório fica em cookie assinado/criptografado, curto, `HttpOnly`.
4. Identity retorna authorization code apenas para callback allowlisted.
5. BFF valida `state`, troca o code server-side e valida assinatura, `iss`, `aud`,
   `exp`, nonce e `token_use` quando aplicável.
6. Tokens do Identity ficam em vault/session store server-side, criptografados em
   repouso; nunca entram em HTML, logs, query strings ou JavaScript.
7. Browser recebe `__Host-appsell_session=<opaque_random_id>` com `Secure`,
   `HttpOnly`, `Path=/` e `SameSite=Lax`; nenhum `Domain` é configurado.
8. O BFF resolve sessão, injeta a identidade server-side e chama a API privada
   com credencial própria e contexto assinado de curta duração.
9. Logout invalida sessão/vault, tenta revogar tokens no Identity e limpa caches
   privados; falha de revogação gera alerta sem manter a sessão local ativa.

## RBAC

Papéis globais persistidos no AppSell:

- `platform_superadmin`
- `platform_support`
- `platform_auditor`

Papéis por seller:

- `seller_owner`
- `seller_admin`
- `seller_editor`
- `seller_support`

`buyer` não é grant administrativo. A condição de comprador deriva de uma
identidade com entitlements, sem acesso administrativo implícito.

Autorização segue `deny by default` e verifica, em cada operação:

```text
identidade autenticada
AND sessão ativa
AND ação permitida pelo grant
AND recurso pertence ao seller esperado
AND invariantes de estado do recurso
```

O catálogo completo está na [matriz de autorização](../AUTHORIZATION_MATRIX.md).

## Bootstrap do superadmin

`comercial@autonomia.site` é um seletor de bootstrap, não uma regra permanente:

1. endpoint/configuração indisponível por padrão;
2. habilitação temporária em ambiente controlado;
3. validar token criptograficamente e exigir issuer/audience/client corretos;
4. exigir `email_verified=true` e igualdade normalizada exata com o e-mail de
   bootstrap;
5. exigir evidência de MFA/passkey adequada ao assurance level aprovado;
6. dentro de transação, garantir que não há bootstrap consumido nem superadmin;
7. persistir o `identity_sub` imutável e o grant `platform_superadmin`;
8. persistir auditoria com actor, target, request id, origem minimizada e data;
9. consumir o bootstrap com unique constraint/lock; repetição retorna conflito;
10. remover/desabilitar a configuração temporária.

Mudança futura de e-mail não altera o grant. Grants posteriores de superadmin
exigem fluxo auditado e dupla aprovação; não reutilizam o e-mail de bootstrap.

Se MFA/passkey não puder ser comprovado no Identity, o bootstrap de produção
permanece bloqueado até uma alternativa de autenticação forte ser aprovada.

## CSRF, origem e sessão

- toda mutação do browser exige token CSRF vinculado à sessão e allowlist exata
  de `Origin`; ausência ou origem irmã não allowlisted falha fechada;
- `Referer` pode complementar, nunca substituir a regra formal;
- webhooks não usam cookie e são exceção explícita: HMAC/mTLS conforme conector;
- rotação de sessão ocorre após login e elevação de privilégio;
- sessão tem idle timeout, absolute timeout e revogação administrativa;
- sessões de superadmin são mais curtas e exigem reautenticação para ações
  destrutivas ou grants;
- endpoints de login/callback possuem rate limit e proteção contra replay.

## Contexto entre BFF e API

O header de contexto não é confiável por estar presente. O BFF autentica-se como
serviço e envia um envelope curto assinado contendo no mínimo:

- `session_id` opaco ou sua referência;
- `identity_sub`;
- `seller_organization_id` selecionado, quando aplicável;
- `issued_at`, `expires_at`, `request_id` e audience da API.

A API rejeita chamada pública, envelope expirado, audience incorreta, replay
quando relevante e seller não autorizado. Grants são carregados da fonte do
AppSell; não são aceitos do browser ou copiados cegamente de claims externos.

## Alternativas rejeitadas

- JWT em `localStorage`/`sessionStorage`: expõe tokens a XSS e contraria a sessão
  opaca requerida.
- JWT do Identity como cookie direto: dificulta revogação e mantém token bearer
  no user agent.
- autorização por e-mail: atributo mutável e potencialmente não verificado.
- confiar em qualquer subdomínio `*.autonomia.site`: same-site não equivale a
  same-origin nem a confiança.
- usar permissões fixas do Admin: backend atual não oferece escopo seguro.

## Validação obrigatória

- login/callback/refresh/logout completos com cliente dev real;
- state, nonce, verifier, code e sessão não podem ser reutilizados;
- callback, issuer, audience, client ou token_use incorretos falham fechados;
- e-mail não verificado não executa bootstrap;
- alteração de e-mail não eleva/revoga papel;
- token CSRF de outra sessão e origem irmã são negados;
- nenhum token aparece em storage, DOM, bundle, logs ou tráfego browser→API;
- revogação administrativa encerra todas as sessões selecionadas;
- testes de confused deputy entre tenants e entre BFF/API.

## Consequências

O BFF e o vault tornam-se componentes críticos e precisam de HA, criptografia,
rotação e observabilidade. Em troca, o browser não possui bearer tokens dos
serviços corporativos e a revogação é controlável pelo AppSell.
