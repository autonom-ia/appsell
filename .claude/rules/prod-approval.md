# Production Approval Gates

Parar e pedir aprovação explícita do Rodrigo ANTES de executar qualquer um destes:

## Sempre requer aprovação
- Qualquer escrita em banco de produção (INSERT, UPDATE, DELETE, ALTER, DROP)
- Alteração de secret, credencial, API key, token
- Deploy em produção ou staging
- Merge de qualquer PR
- Rotação de secrets ou certificados
- Alteração de DNS, permissões IAM, billing, auth
- Restart de serviço em produção
- Qualquer operação com `destroyService`, `deleteService`, `purge`, `wipe`, `truncate` em produção

## Nunca executar (requer decisão humana)
- Force push em `main` / `master`
- `git reset --hard` com mudanças não commitadas
- Drop de tabela ou banco de produção sem backup confirmado
- Alteração de Traefik / Easypanel que afete múltiplos serviços
