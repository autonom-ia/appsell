# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 web + API/PDF sandbox containers locais verdes | 2026-07-12 |
| Testes | 🟢 baseline Local/CI de 100 tenants + suítes anteriores verdes | 2026-07-12 |
| CI | 🟢 PRs #35/#36/#37/#39 e #41 verdes | 2026-07-12 |
| Deploy produção | 🟡 não configurado/não executado | 2026-07-10 |
| Harness instalado em `main` | 2.0.2 | 2026-07-10 |
| Harness upgrade | 🟢 PR #37 em 2.1.2, review e 10/10 checks verdes; sem merge | 2026-07-11 |

## Estado atual

O protótipo Studio mobile-first foi mergeado pela PR #23, a fundação
persistente pela PR #25, a ponte BFF/API pela PR #27 e os drafts persistentes
pela PR #29 e edição/publicação pela PR #31. A Fase 5 está ativa na Issue #38
para player, progresso e retomada multitenant, empilhada nas PRs #35 e #36.
A fundação inclui FastAPI, PostgreSQL 16, Alembic forward-only,
roles owner/migrator/runtime, RLS forçado, contexto transacional assinado,
auditoria append-only e bootstrap one-shot do superadmin com atestação separada.
Este marco é Local/CI; não é o Identity real e não concede conta de produção.

## Última mudança relevante

2026-07-12 — Issue #44 implementada localmente na branch
`fix/44-postgres-startup-headroom`: stability gate PostgreSQL read-only antes da
migration, máximo de duas falhas transitórias e três probes consecutivas;
fault injection descartável comprovou shutdown/restart e teardown 0/0/0;
artifacts usam códigos allowlist e scanner pré-upload. A vertical local de 100
tenants passou com margem acima do orçamento mínimo de 9 slots/40%, sem erro de
sampler. Métricas exatas permanecem no artifact do run e só serão usadas para
calibração após CI em source limpo. Este orçamento é somente Local/CI e não
define sizing de produção nem comprova worker dedicado concorrente, staging ou
HA.

2026-07-12 — Issue #42 implementada localmente na branch
`feat/42-capacity-100-tenants`: 100 organizações, owners, produtos publicados,
buyers e entitlements determinísticos; 200 sujeitos OAuth; BFF/API/PostgreSQL/
Redis reais; vertical S3 real; carga concorrente 10; p50/p95/p99/max,
throughput, RSS, conexões e hardware registrados. O run local passou com
100/100 catálogos, 100/100 mutações de produto, 100/100 bibliotecas, 100/100
assets autorizados e 200/200 tentativas cross-tenant negadas em 404, sem 5xx,
timeout ou payload cruzado. Isso é baseline Local/CI, não certificação de
staging, VPS ou produção.

2026-07-12 — Issue #40 implementada localmente na branch
`feat/40-private-asset-upload`: migration 0008, upload direto S3-compatible,
quarentena, verificação PDF isolada, worker, publicação imutável e download
autorizado por entitlement. Gates reais: 116 Vitest, suíte Python, 148 PG/API,
3 verticais BFF, 3 verticais storage, 31 Playwright focados + 5 skips
deliberados, build e audits verdes. PR #41 aberta e os três checks remotos
verdes no SHA `0b416e4`.

## O que está funcionando

- OAuth/BFF local — state, nonce, PKCE, sessão opaca e browser sem token;
  evidência nas PRs #11/#15 e App CI.
- Redis Local/CI — TTL, Lua atômico, retry idempotente, rate limit, dois BFFs,
  Redis efêmero autenticado; 23/23 testes.
- Modo `production` local (`next start`) fail-closed — mock 404,
  login/callback 503 e sessão 401; 1/1 E2E.
- #17 apenas Local/CI — envelope encryption com DEK por registro, keyring
  local, AAD, sessão+vault atômico e 35/35 testes; reviews AppSec e concorrência
  verdes; PR #20 e App CI pós-merge verdes.
- Refresh distribuído Local/CI mergeado pela PR #21.
- Design system próprio derivado dos tokens do Studio, sem dependência runtime.
- Gate server-side para `/dashboard`, `/products` e `/library`, sujeito dev
  exato e produção fail-closed.
- Projeções demonstrativas testadas: seller não recebe produto de outro seller;
  buyer só recebe entitlement ativo/publicado; recomendações nunca incluem
  draft nem outro seller.
- Mobile/desktop: 360, 390, 768, 1024 e 1440 sem overflow; alvos >= 44 px;
  Chromium e WebKit, reduced motion, deep link e reload verdes.
- PostgreSQL 16 isolado com PUBLIC CONNECT revogado, runtime sem ownership,
  `SUPERUSER` ou `BYPASSRLS`, RLS e grants mínimos verificados diretamente.
- Bootstrap local one-shot: issuer/audience/client fixados, claims vinculados a
  atestação HMAC exclusiva, e-mail verificado exato, MFA/passkey, advisory lock,
  um vencedor entre 20 concorrentes, audit e auto-disable.
- CI Python com lock, Ruff, mypy strict, pip-audit, migration limpa, testes PG,
  build do container por digest e healthcheck real.
- Ponte BFF→API Local/CI com credencial de serviço e envelope HMAC vinculado a
  método, path, query, body, capability, sessão, identidade e janela temporal.
- Replay persistente no PostgreSQL, UUIDv5 compatível em TypeScript/Python/SQL,
  sincronização issuer-scoped e onboarding seller idempotente/atômico.
- POST de onboarding protegido por origem canônica, CSRF por sessão+ação e
  leitura streaming limitada a 16 KiB; fluxo real persiste entre dois BFFs.
- Produto/curso/oferta editáveis com versões esperadas, receipt idempotente e
  locks por produto sem serializar todo o tenant.
- Preview privado e publicação multi-course gerados server-side; snapshot
  idêntico é negado no PostgreSQL e retry exato retorna o receipt original.
- Editor 320 px com rebase de conflito, guard de Back/gesture, modal com foco
  contido, 200% de texto e oferta explicitamente sem cobrança.
- Currículo Course → Module → Lesson text-only com RLS forçado, runtime sem DML,
  CAS por `course.draft_version`, retry idempotente, archive e reordenação por
  conjunto completo; UI usa botões/teclado e preserva foco.
- Receipts curriculares têm janela operacional explícita de até 30 dias e
  retenção máxima de 5.000 por curso; retries fora dessa janela são nova
  operação e continuam sujeitos a versão esperada/autorização.
- Snapshot schema v2 determinístico inclui módulos/aulas; snapshots v1 continuam
  imutáveis. Payload >1 MiB falha em 422 antes de publication/receipt parcial.
- Tenant demo Local/CI usa reset sob advisory lock, IDs determinísticos e duas
  personas fixas; o buyer recebe apenas entitlement ativo e o admin permanece
  fora da biblioteca do comprador.
- Cinco PDFs do curso Codex ficam fora de `public/`; cada leitura exige sessão,
  capability, entitlement, snapshot publicado e validação de hash/tamanho.
- Upload PDF seller-scoped usa intent curta, chave opaca, checksum SHA-256,
  quota transacional e objeto privado versionado; o browser nunca recebe
  credencial S3 e não consegue tornar um asset `ready` diretamente.
- Worker separado move o objeto de quarentena somente após conferir tamanho,
  hash, estrutura PDF e resultado do antimalware; staging/produção falham
  fechados sem ClamAV configurado.
- Publicação fixa `asset_version_id`, hash e tamanho no snapshot. Download do
  comprador revalida sessão, entitlement, publicação e origem do storage.
- Auditoria externa comprova acesso anônimo negado no endpoint S3-compatible;
  reconciliação e cleanup são duráveis e tenant-scoped.
- Uploader mobile permite cancelar, repetir e arquivar com gate de navegação,
  foco preservado, alvos de 44 px e cobertura em 320/360/390 px e texto 200%.
- Player retoma por aula/curso, grava progresso com versão esperada e receipt
  persistente, revalida revogação e não confia em seller vindo do browser.
- Testes cobrem mesma chave, versões concorrentes, revogação, republicação
  pinada, multi-course e 100 buyers sem bypass de RLS.

## O que está quebrado / em degradação

- Identity real — sem dev/hml isolado e com P0 upstream — Issue #13.
- Refresh real com Identity — bloqueado por #13.
- KMS real/vault HA — provider/infra/staging não decididos — Issue #19.
- Staging/VPS/TLS — não certificado e fora do escopo atual — Issue #5.
- Assets privados da Issue #40 estão na PR #41, com review crítico e CI verdes;
  ainda dependem de aprovação/merges ordenados e permanecem somente Local/CI.
- A baseline de regressão Local/CI para uma instalação com 100 tenants está
  comprovada na Issue #42, mas capacidade de lançamento ainda não está
  certificada em staging/VPS: faltam carga com margem 2x, soak, restore,
  rollback e observabilidade reais.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Aguardar review crítico, CI e PR da #42; depois, decisão humana e merges
empilhados #35 → #36 → #39 → #41 → futura PR #42, sem deploy.
Identity real (#13), KMS (#19), billing/Financial, storage externo de staging e
produção continuam bloqueados.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
