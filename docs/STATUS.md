# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 web + API/PDF sandbox containers locais verdes | 2026-07-12 |
| Testes | 🟢 116 web + suíte Python + 148 PG/API + 3 BFF + 3 storage; 31 browser focados | 2026-07-12 |
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
- Capacidade de lançamento para 100 tenants/clientes ainda não está certificada:
  os testes cobrem 100 buyers no banco e 100 requisições concorrentes full-path,
  mas não um soak/load test de 100 tenants com p95/p99 em staging.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Aguardar decisão humana e merges empilhados #35 → #36 → #39 → #41, sem deploy.
Identity real (#13), KMS (#19), billing/Financial, storage externo de staging e
produção continuam bloqueados.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
