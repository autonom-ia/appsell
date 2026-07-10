# MEMORY_PROJECT.md — Contexto do projeto

Atualizado quando arquitetura, integrações ou ambientes mudarem.

## Objetivo do projeto

Construir o Autonom.ia Sell, SaaS white-label mobile-first para produtores
publicarem produtos digitais e áreas de membros, com compradores globais que
podem acessar produtos de mais de um seller sem quebrar o isolamento de tenant.

## Arquitetura resumida

O slice implementado é um Next.js 16/React 19 com BFF OAuth, cookie de sessão
opaco e rotas runtime usando exclusivamente Redis isolado em Local/CI; classes
in-memory permanecem apenas em testes. O mock de Identity é somente local e
falha fechado em modo `production`. A direção aceita
com gates adiciona API modular FastAPI, PostgreSQL, worker/outbox, storage S3 e
fronteiras com Identity, Admin e Financial; esses componentes ainda não foram
materializados. Ver [arquitetura](../architecture/README.md) e
[progresso](../PROGRESS.md).

## Comandos principais

```bash
# Instalar exatamente o lockfile
corepack pnpm install --frozen-lockfile

# Rodar localmente
cp .env.example .env.local
# Configurar Redis NOVO/isolado, nunca a porta ou instância existente.
corepack pnpm dev

# Rodar testes
corepack pnpm test
corepack pnpm test:e2e

# Build / typecheck / lint
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
```

## Integrações

| Sistema | Tipo | Risco | Doc |
|---------|------|:-----:|-----|
| Redis isolado | Sessão/state/cache Local e CI | Alto | [README](../../README.md) / [ADR 0002](../architecture/adr/0002-identity-bff-rbac.md) |
| Autonom.ia Identity | OAuth real | Alto — bloqueado | [Issue #13](https://github.com/autonom-ia/appsell/issues/13) |
| PostgreSQL | Dados multi-tenant futuros | Alto — não implementado | [Modelo de dados](../architecture/DATA_MODEL.md) |
| Autonomia Admin | Catálogo corporativo futuro | Alto — não conectado | [ADR 0005](../architecture/adr/0005-jobs-webhooks-financial-boundary.md) |
| Autonomia Financial | Billing SaaS futuro | Alto — não conectado | [ADR 0005](../architecture/adr/0005-jobs-webhooks-financial-boundary.md) |

## Ambientes

| Ambiente | URL | Branch | Deploy |
|----------|-----|--------|--------|
| Local | `http://localhost:3000` | qualquer | manual |
| Dev compartilhado | não provisionado | `main` | não configurado |
| Staging | não provisionado | não definida | não configurado |
| Produção | `sell.autonomia.site` (alvo; não publicado) | não definida | proibido sem aprovação explícita |

## Riscos conhecidos

- Identity real sem dev/hml e com bloqueios P0 upstream — manter a Issue #13
  bloqueada; não transformar mock em integração de produção.
- Tenancy, RBAC, KMS/vault, refresh distribuído e dados de domínio ainda não
  existem — implementar por slices com testes negativos e revisão independente.
- VPS/TLS/capacidade não estão aprovados para AppSell — nenhuma ação em produção
  nesta fase; a medição read-only da Issue #5 não autoriza deploy.
- Gate npm do scaffold está verde, mas licenças, SBOM, imagens e dependências não
  npm ainda não foram certificados.

## O que NÃO fazer

- Sem merge sem aprovação Rodrigo
- Sem deploy, DNS, secrets, auth real, billing ou dados de clientes sem aprovação
  explícita de Rodrigo e plano de rollback verificado
- Sem alteração de dados sem migration, backup/restore e rollback exercitados
- Sem operação destrutiva em Easypanel sem backup confirmado (Regra 001 MEMORY_CORE)
- Sem omitir campo `env` em updateEnv (Regra 002 MEMORY_CORE)
- Sem reutilizar Redis, Postgres, portas, volumes ou secrets já existentes na VPS
- Sem tratar build, mock ou teste local como evidência de produção
