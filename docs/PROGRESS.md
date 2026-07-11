# PROGRESS

## Fase atual

Fase 4C — currículo multitenant e snapshot v2 em Local/CI.

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
- PR #31 mergeada em `b4f366a`; edição, oferta, preview e publicação imutável
  verdes em CI e pós-merge, Issue #30 encerrada sem deploy.

## Em andamento

- Issue #32 em implementação na branch `feat/32-curriculum`;
- migration 0005 cria módulos/aulas text-only, FKs compostas, RLS forçado,
  receipt privado, CAS/locks por curso, archive e reorder exact-set;
- publicação gera snapshot schema v2 determinístico e preserva v1; limite de
  1 MiB retorna P4221/422 antes de hash, receipt ou insert parcial;
- API/BFF seller-scoped usam envelope de path até 512 caracteres, CSRF separado
  por create/update/reorder/archive e parser limitado;
- UI adiciona etapa Currículo, formulários independentes, botões Subir/Descer,
  foco preservado, dirty guard, 320 px e 200% de texto;
- receipts curriculares retidos por até 30 dias e no máximo 5.000 por curso;
- gates locais: 69 web, 94 PG/API, BFF vertical com duas instâncias, 26
  Playwright + 4 skips, build, production fail-closed e audits verdes;
- PR #35 aberta; aguardando App CI e review do SHA remoto; nenhum deploy;
- Issue #33 implementada localmente em branch empilhada: tenant demo resetável,
  admin/comprador `@autonomia.site`, entitlement buyer, biblioteca persistente e
  curso Codex com cinco PDFs protegidos e verificados;
- épica #8 permanece em desenvolvimento;
- Identity real #13 permanece bloqueado por ausência de dev/hml e P0 upstream.

## Pendente

- spike com Autonom.ia Identity real (#13);
- refresh real contra Identity (#18), bloqueado pelo contrato/ambiente da #13;
- KMS real, vault durável/HA e restore (#19), bloqueado;
- assets privados e editor de blocos ricos após o currículo text-only;
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

Abrir PR da Issue #32, passar reviews críticos e CI remoto. Não declarar acesso
real de `comercial@autonomia.site`: isso depende do Identity #13 e de ambiente
isolado com token/JWKS/MFA reais.
