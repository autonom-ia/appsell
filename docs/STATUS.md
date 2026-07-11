# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 web + API container local verdes | 2026-07-11 |
| Testes | 🟢 74 web + 103 Python/PG/API + 2 verticais BFF + 26 browser; build verde | 2026-07-11 |
| CI | 🟢 PR #35 inteira verde; #33 ainda local/empilhada | 2026-07-11 |
| Deploy produção | 🟡 não configurado/não executado | 2026-07-10 |
| Harness instalado | 2.0.2 | 2026-07-10 |
| Harness drift | ⚠️ 2.0.2 → 2.1.2 | 2026-07-11 |

## Estado atual

O protótipo Studio mobile-first foi mergeado pela PR #23, a fundação
persistente pela PR #25, a ponte BFF/API pela PR #27 e os drafts persistentes
pela PR #29 e edição/publicação pela PR #31. A Fase 4C está ativa na Issue #32
para currículo multitenant com módulos, aulas de texto e snapshot schema v2.
A fundação inclui FastAPI, PostgreSQL 16, Alembic forward-only,
roles owner/migrator/runtime, RLS forçado, contexto transacional assinado,
auditoria append-only e bootstrap one-shot do superadmin com atestação separada.
Este marco é Local/CI; não é o Identity real e não concede conta de produção.

## Última mudança relevante

2026-07-11 — PR #35 de currículo aberta com todos os checks verdes, sem merge
ou deploy. Branch empilhada `feat/33-demo-tenant`: migration 0006, entitlement
buyer, assets privados, tenant resetável, personas admin/aluno `@autonomia.site`
e curso Codex com cinco PDFs. Gates locais: 74 web, 103 Python/PG/API (incluindo 100
buyers sob paralelismo limitado), 2 verticais BFF/API/PG com 100 leituras de
biblioteca e 100 downloads concorrentes, 26 Playwright + 4
skips deliberados, build, audit e production fail-closed verdes.

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

## O que está quebrado / em degradação

- Identity real — sem dev/hml isolado e com P0 upstream — Issue #13.
- Refresh real com Identity — bloqueado por #13.
- KMS real/vault HA — provider/infra/staging não decididos — Issue #19.
- Staging/VPS/TLS — não certificado e fora do escopo atual — Issue #5.
- Tenant/admin/comprador demo e curso Codex estão implementados somente na
  branch local da Issue #33; ainda dependem de review, PR, CI e merge de #35.
- Capacidade de lançamento para 100 tenants/clientes ainda não está certificada:
  os testes cobrem 100 buyers no banco e 100 requisições concorrentes full-path,
  mas não um soak/load test de 100 tenants com p95/p99 em staging.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Concluir reviews críticos da Issue #33, abrir PR empilhada sobre #35 e executar
CI no SHA remoto. Identity real (#13), KMS (#19), billing/Financial e produção
continuam bloqueados até ambientes e contratos reais.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
