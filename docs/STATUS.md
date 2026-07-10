# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 main verde; branch #18 ainda em validação | 2026-07-10 |
| Testes | 🟢 main 35+6+1; branch #18 local 51+6+1 | 2026-07-10 |
| CI | 🟢 main verde | 2026-07-10, run `29106000267`, SHA `90a87c9` |
| Deploy produção | 🟡 não configurado/não executado | 2026-07-10 |
| Harness instalado | 2.0.2 | 2026-07-10 |
| Harness drift | OK | 2026-07-10 |

## Estado atual

Foundation local em Next.js com sessão opaca, Redis isolado e vault cifrado
Local/CI foi mergeada pela PR #20. A PR draft #21 contém o candidato Local/CI
de refresh distribuído, com reviews verdes e App CI pendente. O mock
Identity e os providers locais continuam bloqueados em produção; não existe
staging/produção do AppSell certificado ou autorizado.

## Última mudança relevante

2026-07-10 — PR [#20](https://github.com/autonom-ia/appsell/pull/20), merge
`90a87c9`: token vault cifrado Local/CI; App CI pós-merge verde.

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
- Candidato #18 Local/CI — refresh server-side com lease, fencing, intent e CAS;
  16 testes adversariais novos, 51/51 Vitest locais; reviews AppSec,
  concorrência e contrato verdes, P0-P2=0.

## O que está quebrado / em degradação

- Identity real — sem dev/hml isolado e com P0 upstream — Issue #13.
- Refresh real com Identity — bloqueado por #13; a prova Local/CI segue na #18.
- KMS real/vault HA — provider/infra/staging não decididos — Issue #19.
- Staging/VPS/TLS — não certificado e fora do escopo atual — Issue #5.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Aguardar e concluir o App CI da PR #21. Manter
a integração Identity real (#13), KMS real (#19) e qualquer ação em produção
bloqueados.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
