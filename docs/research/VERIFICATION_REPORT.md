# Relatório de Verificação — 2026-07-10

## Escopo

Verificação read-only do pacote documental, ativos GitHub da Autonom.ia, domínio informado, contratos de integração e testes existentes antes da elaboração dos planos.

## Fontes GitHub inspecionadas

| Repositório | SHA |
|---|---|
| `autonom-ia/studio` | `a0e2759c514e` |
| `autonom-ia/autonomia-identity-auth` | `72618e9cba53` |
| `autonom-ia/autonomia-identity-sdk` | `bf366ccdcebf` |
| `autonom-ia/autonomia-financial` | `359ce108e23a` |
| `autonom-ia/autonomia-financial-sdk` | `eb11fae288b4` |
| `autonom-ia/autonomia-admin-sdk` | `a948d6b25b0a` |
| `autonom-ia/neuroai-web` | `cc631259fdba` |
| `autonom-ia/autonomia-admin` | `1b3c0168f89e` |
| `autonom-ia/autonomia-identity-foundation` | `19fee10b6570` |

## Evidências principais

### Mapa de evidência de código

| Afirmação | Evidência |
|---|---|
| Studio é referência visual | `studio@a0e2759:docs/DESIGN_SYSTEM.md:5-146` |
| OAuth usa Code + PKCE e callback allowlisted | `autonomia-identity-foundation@19fee10:services/auth-api/src/domain/oauth/oauth-client.service.ts:90-197` |
| Auth SDK pode persistir tokens no browser | `autonomia-identity-sdk@bf366cc:packages/auth-core/src/client.ts:64-92` |
| AdminProduct é produto corporativo/OAuth | `autonomia-admin-sdk@a948d6b:src/core.ts:59-109` |
| Admin atual concede permissões/vínculos amplos | `autonomia-admin@1b3c016:src/routes.ts:105-129,153-378`; `src/repository.ts:103-136,211-233` |
| Financial admin não é escopado por operator | `autonomia-financial@359ce10:src/routes.ts:417-721,1366-1399` |
| Sync financeiro corporativo usa Autonom.ia | `autonomia-financial@359ce10:src/financial-sync-consumer.ts:6-27,79-110` |
| Neuro monta módulos por SDK no shell | `neuroai-web@cc63125:src/app/auth-shell.tsx` |

### Studio

- Design system documentado e identidade visual consistente como direção.
- `packages/ui` ainda não fornece pacote reutilizável completo.
- shell mobile atual é adaptação responsiva; AppSell precisa de navegação mobile própria.
- padrões úteis de container, non-root, health check e rede privada.
- padrões de CI/deploy que não devem ser copiados: lint não bloqueante, tag `latest` e produção automática em `main`.

### Identity

- backend implementa OAuth Authorization Code + PKCE S256;
- callback allowlisted, code curto e de uso único;
- SDK suporta sessão, mas pode armazenar access/refresh token em `localStorage`;
- AppSell deve realizar callback e refresh no BFF com cookie `HttpOnly`.

### Admin

- AdminProduct representa produto corporativo/OAuth da Autonom.ia, não curso de seller;
- backend atual aceita qualquer JWT válido no pre-handler;
- `/admin/me` devolve permissões fixas amplas;
- novos usuários recebem vínculo privilegiado padrão;
- sellers e buyers não podem acessar esse backend no estado atual.

### Financial

- existem operadores, catálogo, planos, preços, coupons, gateways, subscriptions, invoices e webhook Stripe;
- operadores podem se vincular a organization;
- autorização administrativa ainda é global por `financial.admin`;
- não existe scoping seguro principal organization -> operator;
- sync corporativo usa operator Autonom.ia;
- adequado para billing SaaS inicial, inadequado para seller commerce direto sem hardening.

### Neuro.ai

- página local de produtos é apenas rota/placeholder;
- UI real é montada por Admin/Financial SDKs no shell;
- não copiar a página; integrar contratos apropriados;
- uso de dependências `latest` no deploy não deve ser reproduzido.

## Testes reais observados durante o discovery

| Componente | Resultado |
|---|---|
| Identity SDK | 3 testes passaram; build/typecheck passaram após build do core |
| Admin SDK | 3 testes, typecheck e build passaram |
| Financial SDK | 5 testes, typecheck e build passaram |
| Financial | 147 testes, typecheck e build passaram |
| Neuro.ai | 5 testes, typecheck e build passaram |
| Identity Auth | build passou; suíte retornou `No test files found` |
| Admin API | três testes dependem de `DATABASE_URL`; não cobrem permissão/tenancy |
| Financial install audit | 16 vulnerabilidades: 8 moderadas, 4 altas e 4 críticas |

Esses resultados não certificam produção. Faltam ambientes efêmeros completos, contratos reais entre serviços, testes adversariais de autorização e triagem das vulnerabilidades.

## Domínio e infraestrutura

Verificação pública não intrusiva:

- `sell.autonomia.site` -> `85.31.60.100`;
- HTTP -> HTTPS;
- certificado HTTPS autoassinado `CN=Easypanel`;
- resposta atual `404` do Easypanel;
- TLS 1.2/1.3 disponíveis;
- sem scan de portas;
- nenhuma alteração em DNS, servidor ou certificado.

Há drift na documentação do Studio: seus docs ainda mencionam `85.31.60.100`, mas os hosts públicos atuais do Studio resolvem para outro IP e usam Caddy/Let's Encrypt. Portanto, documentação histórica não prova capacidade ou topologia atual do host do AppSell.

## Harness

- repositório criado: https://github.com/autonom-ia/appsell;
- Issue: https://github.com/autonom-ia/appsell/issues/1;
- PR: https://github.com/autonom-ia/appsell/pull/3;
- oito checks do GitHub Actions passaram;
- Validator: 0 faltantes;
- Doctor: 0 errors, 0 warnings;
- revisão independente encontrou e fechou falso positivo no hook e vazamento potencial no secret scan;
- defeito upstream registrado em https://github.com/autonom-ia/agent-harness/issues/13;
- PR permanece sem merge.

## Conclusão

Os ativos internos reduzem tempo de entrega, mas não podem ser conectados diretamente sem fronteiras. A recomendação segura é:

- Studio como linguagem visual;
- Identity como OAuth, com sessão BFF;
- Admin para produto corporativo AppSell;
- Financial para billing SaaS inicial;
- catálogo, ofertas, conteúdo, buyers e entitlements como domínio multi-tenant próprio do AppSell;
- checkout externo + webhook no MVP até o Financial ganhar scoping por seller/operator.
