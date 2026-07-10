# appsell

Plataforma SaaS white-label mobile-first para produtos digitais e áreas de membros da Autonom.ia

## Documentação

- [Plano Executivo](docs/plans/EXECUTIVE_PLAN.md)
- [Plano Técnico](docs/plans/TECHNICAL_PLAN.md)
- [Relatório de Verificação](docs/research/VERIFICATION_REPORT.md)
- [Arquitetura, ADRs e threat model](docs/architecture/README.md)

## Spike OAuth/BFF local

O spike demonstra OAuth Authorization Code + PKCE com callback no BFF e sessão
opaca. As rotas usam somente Redis, com TTL e consumo atômico; não existe
fallback para memória. O mock de Identity continua exclusivamente local e
recusa execução quando `NODE_ENV=production`.

```bash
corepack pnpm install
cp .env.example .env.local
# Ajuste para um Redis NOVO/isolado; não use a porta 6379 ou instância existente.
corepack pnpm dev
```

Abra `http://localhost:3000/auth/login`. O fluxo termina em `/dashboard`, sem
code ou token na URL final. O browser recebe apenas cookies opacos `HttpOnly`.

Validação:

```bash
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
corepack pnpm test:e2e
```

`APPSELL_REDIS_URL`, `APPSELL_REDIS_PREFIX` e
`APPSELL_SESSION_HMAC_KEY_B64` são obrigatórios. Gere a chave local com
`openssl rand -base64 32`; ela deriva o ID de sessão idempotente via HMAC e não
é persistida no Redis. O prefixo deve
seguir `appsell:<ambiente>:<instância-única>`. IDs opacos só aparecem nas chaves
como SHA-256; transaction, code e sessão expiram por TTL do Redis. A suíte de
integração e o E2E criam um `redis-server` efêmero em porta aleatória diferente
de 6379, protegido por senha aleatória. O harness valida o PID do processo antes
de testar e aguarda seu encerramento antes de remover o diretório temporário.

Limites deliberados do spike:

- não há Identity externo, refresh real, HA/failover ou criptografia KMS;
- a sessão Redis contém somente identidade e metadados sanitizados; access e
  refresh tokens não são persistidos nesta etapa;
- o mock `/dev/identity/*` não é uma implementação de produção;
- nenhuma autorização multi-tenant/RBAC está implementada nesta etapa.
