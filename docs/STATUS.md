# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 main e branch #17 locais verdes | 2026-07-10 |
| Testes | 🟢 main 23+6+1; branch #17 local 35+6+1 | 2026-07-10 |
| CI | 🟢 main verde | 2026-07-10, run `29103657435`, SHA `ad784e2` |
| Deploy produção | 🟡 não configurado/não executado | 2026-07-10 |
| Harness instalado | 2.0.2 | 2026-07-10 |
| Harness drift | OK | 2026-07-10 |

## Estado atual

Foundation local em Next.js com sessão opaca e Redis isolado foi mergeada. A
branch #17 adiciona um candidato Local/CI de vault cifrado, ainda sem PR/review
final. O mock Identity e o provider local continuam bloqueados em produção; não
existe staging/produção do AppSell certificado ou autorizado.

## Última mudança relevante

2026-07-10 — PR [#16](https://github.com/autonom-ia/appsell/pull/16), merge
`ad784e2`: documentação/decisões sincronizadas; App CI pós-merge verde.

## O que está funcionando

- OAuth/BFF local — state, nonce, PKCE, sessão opaca e browser sem token;
  evidência nas PRs #11/#15 e App CI.
- Redis Local/CI — TTL, Lua atômico, retry idempotente, rate limit, dois BFFs,
  Redis efêmero autenticado; 23/23 testes.
- Modo `production` local (`next start`) fail-closed — mock 404,
  login/callback 503 e sessão 401; 1/1 E2E.
- Candidato #17 apenas Local/CI — envelope encryption com DEK por registro,
  keyring local, AAD, sessão+vault atômico e 35/35 testes; reviews AppSec e
  concorrência verdes, PR/CI pendentes.

## O que está quebrado / em degradação

- Identity real — sem dev/hml isolado e com P0 upstream — Issue #13.
- Refresh distribuído — bloqueado por #17/#13 — Issue #18.
- KMS real/vault HA — provider/infra/staging não decididos — Issue #19.
- Staging/VPS/TLS — não certificado e fora do escopo atual — Issue #5.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Concluir reviews e PR da #17. Manter #18 e #19 bloqueadas e sem qualquer ação em
produção.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
