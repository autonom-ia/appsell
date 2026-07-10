# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 verde | 2026-07-10, App CI `29101898284` |
| Testes | 🟢 23 Vitest + 6 browser + 1 modo production local fail-closed | 2026-07-10 |
| CI | 🟢 verde | 2026-07-10, SHA `594b55f` |
| Deploy produção | 🟡 não configurado/não executado | 2026-07-10 |
| Harness instalado | 2.0.2 | 2026-07-10 |
| Harness drift | OK | 2026-07-10 |

## Estado atual

Foundation local em Next.js com sessão opaca e Redis isolado foi mergeada. O
mock Identity continua bloqueado em produção; não existe staging/produção do
AppSell certificado ou autorizado.

## Última mudança relevante

2026-07-10 — PR [#15](https://github.com/autonom-ia/appsell/pull/15), merge
`594b55f`: store Redis fail-closed, idempotente e testado com dois BFFs reais.

## O que está funcionando

- OAuth/BFF local — state, nonce, PKCE, sessão opaca e browser sem token;
  evidência nas PRs #11/#15 e App CI.
- Redis Local/CI — TTL, Lua atômico, retry idempotente, rate limit, dois BFFs,
  Redis efêmero autenticado; 23/23 testes.
- Modo `production` local (`next start`) fail-closed — mock 404,
  login/callback 503 e sessão 401; 1/1 E2E.

## O que está quebrado / em degradação

- Identity real — sem dev/hml isolado e com P0 upstream — Issue #13.
- Staging/VPS/TLS — não certificado e fora do escopo atual — Issue #5.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Concluir a sincronização documental #12; depois abrir Issues próprias para
vault/KMS e refresh distribuído, mantendo Identity real bloqueado.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
