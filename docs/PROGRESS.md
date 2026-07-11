# PROGRESS

## Fase atual

Fase 4B — edição, preview e publicação por snapshot em Local/CI.

## Concluído

- scaffold Next.js 16.2.10 + React 19.2.7;
- OAuth mock HTTP com state, nonce e PKCE S256;
- callback server-side e code single-use;
- sessão opaca com cookie HttpOnly e store Redis dev-only;
- dashboard, introspecção mínima e logout same-origin;
- consumo somente após state/nonce/PKCE válidos;
- stores limitados, limpeza de expirados e rate limit local;
- testes Playwright automatizados em Chromium e WebKit mobile de login, sessão, logout e replay;
- suíte automatizada com `next start` para falha fechada em modo `production`
  local, sem alegação de produção real;
- 23 testes Vitest de replay, config, revogação, TTL, concorrência e dois BFFs reais;
- repository Redis assíncrono sem fallback em memória, IDs SHA-256 e TTL real;
- callback e criação de sessão em uma operação Lua atômica;
- rate limit e revogação atômicos;
- testes reais com Redis efêmero fora da porta 6379, incluindo 20 callbacks concorrentes.
- retry idempotente de callback com ID HMAC determinístico e receipt curto sem IDs crus;
- prova real com dois processos Next BFF distintos compartilhando a mesma sessão;
- Redis de teste autenticado e validado por PID antes de qualquer operação.
- token vault Local/CI com envelope encryption, DEK por registro, revision na
  AAD e keyring rotacionável; PR #20 mergeada e CI pós-merge verde;
- refresh server-side sem rota HTTP, coordenado por lease, fence decimal exato,
  intent idempotente e CAS Lua;
- máquina de estados `prepared/inflight/retryable/ambiguous`; somente transient
  comprovado habilita retry, enquanto crash inflight falha ambíguo;
- resultado upstream ambíguo persistido sem retry automático; `invalid_grant`
  e escritores obsoletos protegidos por revision/fence; logout explícito apaga
  o par e coordenação incondicionalmente;
- 16 testes adversariais de refresh em Redis real, totalizando 51 Vitest locais.
- PR #21 de refresh distribuído mergeada em `main`;
- landing pública qualificada como prévia local;
- shell persistente derivado do Studio, com sidebar desktop e bottom nav mobile;
- visões demonstrativas de produtor, catálogo e comprador multi-seller;
- gate server-side no Proxy antes de renderizar conteúdo protegido;
- proteção contra cookie HttpOnly no DOM/RSC e request anônimo sem payload;
- projeções tenant/buyer testadas e fail-closed para produção/sujeito desconhecido;
- matriz real 360/390/768/1024/1440, 44 px, WebKit/Chromium, reload,
  teclado e reduced motion;
- 55 unitários, 17 E2E e 1 teste production local verdes.
- PR #23 do shell Studio mobile-first mergeada em `main`;
- scaffold FastAPI/Python 3.12 com container non-root por imagens digest-pinned;
- PostgreSQL 16, Alembic forward-only e roles owner/migrator/runtime separadas;
- identidade global, grants, seller organization/membership, bootstrap e audit;
- contexto HMAC transacional revalidado pelas policies e RLS forçado;
- bootstrap one-shot com atestação de claims em chave separada, trust policy,
  janela disabled-by-default, auto-disable e 20 concorrentes;
- 7 testes API/config + 25 testes PostgreSQL reais, pip-audit sem vulnerabilidade;
- CI Python/PG/container acrescentado ao App CI, com actions fixadas por SHA.
- PR #25 da fundação PostgreSQL/API mergeada em `main`;
- contrato BFF→API assinado e validado nos dois runtimes, com golden vector;
- replay durável, issuer-scoped identity sync e UUIDv5 compatível em TS/Python/SQL;
- endpoints `PUT/GET /v1/me` e `GET/POST /v1/me/sellers` com capability/RLS;
- onboarding seller idempotente e atômico, persistido no PostgreSQL;
- CSRF por sessão+ação, origem canônica e parser streaming limitado a 16 KiB;
- teste real com dois processos BFF, Redis, FastAPI e PostgreSQL;
- smoke local completo: login 200, dashboard 200, create 201 e list 200;
- 63 unitários web, 48 Python, 43 PostgreSQL/API, 21 E2E e 1 production verdes.
- PR #27 mergeada no commit `95c913e`; App CI pós-merge e Harness verdes;
- Issues #24 e #26 encerradas com evidência, sem deploy.
- PR #29 mergeada em `a2695c7`; produto e curso draft persistentes, App CI
  pós-merge verde e Issue #28 encerrada sem deploy.

## Em andamento

- Issue #30 implementada localmente na branch `feat/30-product-publication`;
- migration 0004 cria offer, publication append-only, receipt privado, RLS e
  publicação multi-course transacional com SQLSTATE estável;
- edição usa optimistic locking, idempotência e locks que preservam revogação
  sem serializar produtos distintos do mesmo tenant;
- BFF/API usam CSRF separado, parser 16 KiB exato, preview privado e retry de
  publicação que retorna o mesmo receipt;
- UI exige tenant na URL, protege dirty state/Back, contém foco nos diálogos,
  rebasa 409 sem perder campos e não simula billing;
- gates locais: 68 web, 84 PG/API, BFF vertical, 24 Playwright + 4 skips,
  build, production fail-closed e dependency audits verdes;
- reviews finais tenancy, API/BFF e UI/UX verdes com P0-P2=0;
- PR/CI remoto ainda pendentes; nenhum deploy;
- épica #8 permanece em desenvolvimento;
- Identity real #13 permanece bloqueado por ausência de dev/hml e P0 upstream.

## Pendente

- spike com Autonom.ia Identity real (#13);
- refresh real contra Identity (#18), bloqueado pelo contrato/ambiente da #13;
- KMS real, vault durável/HA e restore (#19), bloqueado;
- módulos/aulas, editor de blocos e assets privados após a publicação base;
- RLS buyer/entitlements, checkout e biblioteca persistente;
- integrações Neuro.ai e financeiro após contratos/repos reais.

## Falhas encontradas e corrigidas

- cookie HttpOnly aparecia no canal de debug do RSC: dashboard passou a usar
  somente a visão sanitizada de `/api/session` no cliente;
- callback inválido consumia transaction/code antes das validações: consumo foi
  movido para depois de state, nonce e PKCE;
- stores não tinham limites/limpeza/rate limit: controles locais adicionados;
- PostCSS vulnerável: resolução fixada em 8.5.16 e audit zerado;
- rotas dev retornavam 500 no build de produção: agora falham de forma
  controlada, com 404/503/401 e `Cache-Control: no-store`.
- erro de callback ainda removia o cookie de transaction: a remoção agora só
  acontece após sucesso, preservando a tentativa legítima.
- leitura server-side do cookie no RSC dev serializou sessão no Flight inline:
  gate movido para `proxy.ts`, com teste explícito de ausência no DOM/responses;
- Vitest 4.0.18 tinha advisory crítico no UI server de desenvolvimento:
  atualizado para 4.1.0 e `pnpm audit` voltou a zero.
- UUIDv5 SQL inicialmente concatenava NUL em `text`, inválido no PostgreSQL:
  derivação passou a concatenar bytes UTF-8 e ganhou prova cruzada TS/Python/SQL.
- parser do onboarding rejeitava o `charset=UTF-8` legítimo de URLSearchParams:
  media type agora é normalizado sem relaxar origem, CSRF ou limite de 16 KiB.
- CI PostgreSQL não provisionava as novas dependências Node/pnpm/Redis exigidas
  pela integração com dois BFFs: pipeline passou a instalá-las explicitamente e
  ficou verde no PR e pós-merge.

## Próximo passo recomendado

Abrir PR da #30, atualizar Project e executar CI remoto. Não declarar acesso real
de `comercial@autonomia.site`: isso depende do Identity #13 e de ambiente
isolado com token/JWKS/MFA reais.
