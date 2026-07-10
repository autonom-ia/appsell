# appsell

Plataforma SaaS white-label mobile-first para produtos digitais e áreas de membros da Autonom.ia

## Documentação

- [Plano Executivo](docs/plans/EXECUTIVE_PLAN.md)
- [Plano Técnico](docs/plans/TECHNICAL_PLAN.md)
- [Relatório de Verificação](docs/research/VERIFICATION_REPORT.md)
- [Arquitetura, ADRs e threat model](docs/architecture/README.md)

## Spike OAuth/BFF local

O spike da Issue #8 demonstra OAuth Authorization Code + PKCE com callback no
BFF e sessão opaca. O mock de Identity e o vault em memória são exclusivamente
locais e recusam execução quando `NODE_ENV=production`.

```bash
corepack pnpm install
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
```

Limites deliberados do spike:

- estado, codes, sessões e tokens são perdidos ao reiniciar o processo;
- não há Identity externo, refresh real, HA, Redis, banco ou criptografia KMS;
- o mock `/dev/identity/*` não é uma implementação de produção;
- nenhuma autorização multi-tenant/RBAC está implementada nesta etapa.
