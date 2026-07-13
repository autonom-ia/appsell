# PROGRESS

## Fase atual

Fase 5 — entrega de conteúdo e assets privados multitenant em Local/CI.

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

- Issue #46 na branch `feat/46-capacity-1000-buyers`: vertical Local/CI ampliada
  para 100 admins/tenants e 1.000 buyers, com dez buyers e dez entitlements
  distintos por tenant; 1.100 sessões OAuth foram observadas de fato;
- a cobertura ampliada concluiu 1.000 library/player/progress/asset e negações
  cross-tenant de produto, player/progresso e asset, sem 5xx, timeout, payload
  inválido ou acesso cruzado; o certificado final faz 9.500 requests, 4.100 negações
  esperadas, BFF abaixo de 2 GiB, headroom PostgreSQL acima de 40% e teardown
  limpo; as métricas exatas de cada execução ficam no artifact JSON;
- thresholds continuam provisórios até três runs verdes em CI sobre source
  limpo; esta evidência não certifica Identity real, staging, HA ou produção;
- a evidência é de cardinalidade sintética Local/CI, não simultaneidade de 1.100
  usuários, Identity real, staging ou produção; publicação permanece
  condicionada ao gate crítico final e ao CI remoto;

- Issue #40 na branch `feat/40-private-asset-upload`: migration 0008, quotas e
  RLS forçados, upload direto S3-compatible em quarentena, worker separado,
  PDF sandbox sem rede e publicação pinada à versão imutável do objeto;
- download exige sessão opaca, entitlement ativo, publicação exata e URL
  assinada com origem de storage validada; acesso público anônimo é auditado;
- uploader mobile tem progresso, retry idempotente, cancelamento, archive e
  bloqueio seguro de navegação/logout enquanto cancela transferências;
- demo mantém `admin.demo@autonomia.site`, `user.demo@autonomia.site` e o curso
  Codex na prática com cinco PDFs; `comercial@autonomia.site` continua apenas
  como bootstrap local, sem alegação de acesso no Identity real;
- gates locais atuais: 116 Vitest, suíte Python, Ruff/mypy, 148 PG/API, 3 BFF,
  3 storage, 31 Playwright focados + 5 skips deliberados, build e audit Node;
- PR #41 aberta, review crítico P0/P1/P2/P3 zerado e três checks remotos verdes
  no SHA `0b416e4`; aguardando decisão e merges empilhados, sem deploy;

- Issue #38 na branch `feat/38-learning-progress`: migration 0007, player
  mobile-first, progresso monotônico/idempotente e retomada multi-course;
- assets protegidos por entitlement+asset, stream privado e validação de
  tamanho/SHA-256 sem carregar o PDF inteiro em memória;
- isolamento adversarial cobre cross-buyer/tenant/lesson, revogação, snapshot
  pinado, append-only, concorrência e 100 buyers;
- gates locais: 84 web, 137 PG/API, 3 BFF verticais reais, 42 E2E + 4 skips,
  production fail-closed, build e audits verdes;
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
- slice parcial da Issue #33 implementado localmente em branch empilhada: tenant demo resetável,
  admin/comprador `@autonomia.site`, entitlement buyer, biblioteca persistente e
  curso Codex com cinco PDFs protegidos e verificados; comprador demo fica
  restrito server-side a `identity.sync`, `library.read` e `library.asset.read`,
  com cookies CSRF de produtor expirados e sem acesso direto ao workspace
  `/products`;
- admin demo confinado ao seller UUID da tenant demo; onboarding e catálogo
  cross-tenant falham em 403, e o reset remove memberships/grants legados das
  duas personas;
- gates do delta #33: 113 testes Python/PG/API, 74 web, 2 verticais BFF/API/PG,
  30 Playwright + 4 skips deliberados e 1 production fail-closed;
- PR #36 da Issue #33 aberta em Draft, App CI 2/2 verde e review crítico
  P0=P1=P2=P3=0; nenhum merge ou deploy;
- Issue #56 em branch empilhada local adiciona inspeção superadmin temporária e
  read-only da demo: migration 0007, token com hash-only no banco, TTL máximo de
  15 minutos, seller fixo server-side, auditoria append-only e revogação;
- callbacks exigem grant `platform_superadmin` ativo por issuer+sub; a persona
  sintética `comercial@autonomia.site` é oculta e Local/CI, não recebe papel por
  e-mail e não equivale a uma conta Identity real;
- gates locais da Issue #56: 126 Python/PG/API, 83 web, 4 verticais BFF/API/PG,
  30 Playwright + 4 skips deliberados, 1 production fail-closed, build, Ruff,
  mypy strict, lint, typecheck e audits verdes; navegador real 390 px sem
  overflow e sem controles seller;
- primeiro review crítico retornou RED: rotas FastAPI não estavam restritas a
  Local/CI e faltavam provas BFF de POST de catálogo/PATCH de currículo; ambos
  foram corrigidos antes de qualquer commit/push e exigem novo ciclo completo;
- teste adversarial adicional detectou token HttpOnly serializado pelo RSC; a
  página deixou de ler cookies e passou a consumir apenas um GET BFF sanitizado.
  O gate full-path agora prova ausência tanto do token quanto da sessão no HTML;
- segundo review crítico retornou RED porque o callback superadmin ainda emitia
  sessão sem Domain API e as rotas eram enumeráveis no OpenAPI; o fluxo agora
  revoga a sessão/expira cookies/falha em 503 e o OpenAPI foi desabilitado, com
  testes regressivos verdes antes de qualquer commit/push;
- review crítico independente, commit/push, Draft PR e CI remoto da Issue #56
  ainda pendentes; nenhum merge, deploy, VPS, DNS ou produção;
- épica #8 permanece em desenvolvimento;
- Identity real #13 permanece bloqueado por ausência de dev/hml e P0 upstream.

## Pendente

- spike com Autonom.ia Identity real (#13);
- refresh real contra Identity (#18), bloqueado pelo contrato/ambiente da #13;
- KMS real, vault durável/HA e restore (#19), bloqueado;
- storage/ClamAV externos de staging, backup/restore e editor de blocos ricos;
- checkout/billing e storage privado externo para conteúdo de clientes;
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

Concluir as revisões de tenancy e capacidade da Issue #46, corrigir qualquer
P0-P3 e repetir o run exato antes de solicitar autorização para commit/push/PR
empilhada sobre #45. Não declarar acesso real de `comercial@autonomia.site`:
isso depende do Identity #13 e de ambiente isolado com token/JWKS/MFA reais.
