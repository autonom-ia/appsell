# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 web + API container local verdes | 2026-07-11 |
| Testes | 🟢 63 web + 48 API + 43 PG/API + 21 browser + 1 production | 2026-07-11 |
| CI | 🟡 slice #26 validado localmente; PR/CI remoto pendentes | 2026-07-11 |
| Deploy produção | 🟡 não configurado/não executado | 2026-07-10 |
| Harness instalado | 2.0.2 | 2026-07-10 |
| Harness drift | ⚠️ 2.0.2 → 2.1.2 | 2026-07-11 |

## Estado atual

O protótipo Studio mobile-first foi mergeado pela PR #23 e a fundação
persistente pela PR #25. A Issue #26 conecta o BFF à API com envelope assinado,
replay durável, sincronização de identidade e onboarding seller persistente.
A fundação inclui FastAPI, PostgreSQL 16, Alembic forward-only,
roles owner/migrator/runtime, RLS forçado, contexto transacional assinado,
auditoria append-only e bootstrap one-shot do superadmin com atestação separada.
Este marco é Local/CI; não é o Identity real e não concede conta de produção.

## Última mudança relevante

2026-07-11 — branch `feat/26-bff-api-onboarding`: migration limpa, 43 testes
PostgreSQL/API, integração real com dois BFFs, 63 testes web, 21 Playwright,
container non-root, readiness e smoke local login→tenant verdes.

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

## O que está quebrado / em degradação

- Identity real — sem dev/hml isolado e com P0 upstream — Issue #13.
- Refresh real com Identity — bloqueado por #13.
- KMS real/vault HA — provider/infra/staging não decididos — Issue #19.
- Staging/VPS/TLS — não certificado e fora do escopo atual — Issue #5.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Concluir o review crítico da #26, abrir PR vinculada à Issue, executar CI remoto
e atualizar o Project. Depois implementar domínio persistente de produto/curso
e a jornada buyer/entitlements. Identity real (#13), KMS (#19) e produção
continuam bloqueados até ambientes/contratos reais.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
