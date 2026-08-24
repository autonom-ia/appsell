# MEMORY_PROJECT.md — Contexto operacional do projeto

Atualizado em 2026-08-24. Esta memória é um índice curto para retomada. A
fonte detalhada e atual de produção é
[`docs/PRODUCTION_CURRENT.md`](../PRODUCTION_CURRENT.md); não usar uma memória
antiga ou documento histórico para inferir o estado do domínio.

## Objetivo

Autonom.ia Sell é uma plataforma multi-tenant para sellers publicarem cursos,
ebooks e outros produtos digitais. A plataforma tem um
`platform_superadmin`; cada tenant tem seu `seller_owner`; compradores são
identidades globais que podem ter acesso a produtos de vários sellers sem
receber permissão editorial.

## Hierarquia real

```text
Plataforma
└── comercial@autonomia.site — platform_superadmin
    ├── Tenant Autonom.ia — rodrigo.victor@autonomia.site — seller_owner
    └── Tenant Hub2You — comercial@hub2you.ai — seller_owner
```

O superadmin administra a plataforma e pode operar um tenant apenas com
tenant explícito, capability e auditoria. `seller_owner` fica restrito ao
próprio tenant. Buyer não é owner nem editor.

## Produção atual

| Item | Valor verificado |
|---|---|
| Domínio | `https://sell.autonomia.site` |
| Infra | VPS `85.31.60.100`, EasyPanel nativo/Docker Swarm; não AWS/EC2 neste release |
| Release | SHA `2c8a36897eadf624955810ffdaee7b33c6239334` |
| PR / Issue | #201 / #195 |
| CI | run `32656218821` — PASS |
| Banco | PostgreSQL nativo `appsell_native_90` |
| Migration head | `0032_platform_superadmin_catalog` |
| Imagens | `easypanel/sell-autonomia/{web,api}:2c8a3689`; worker usa a imagem API |
| Readiness | API interna `database=true`, `storage=true`, `pdf_sandbox=true` |
| Backup pré-cutover | `/home/claudete/backups/appsell/appsell_native_90-before-2c8a3689-20260823-183653.dump` |
| SHA-256 do backup | `509134d43c1be6f502965b83eecc51eb4f68278ff2a7f0025f7aa5fb93bbea12` |
| Rollback de imagem | tag anterior `64ab9956` preservada |

Produção é real e não usa reset, seed ou personas demo. Scripts de demo são
somente Local/CI/homologação isolada.

## Fluxos de produto e acesso

- Editor: Produto → Currículo → Oferta → Preview → Publicar.
- Material: upload assíncrono, verificação de segurança e publicação somente
  após `ready`; o nome físico do arquivo não é apresentado ao comprador.
- Link gratuito: abre o acesso do produto, autentica/cadastra o buyer e
  concede entitlement de forma idempotente, sem checkout.
- Link pago: não concede biblioteca antes da confirmação do checkout; o
  entitlement depende do fluxo pago/fulfillment. Ativação de billing live é
  um gate separado e não deve ser presumida a partir do catálogo publicado.
- Conteúdo privado: player e PDF validam buyer + seller + entitlement; não
  são arquivos públicos nem ficam disponíveis ao seller de outro tenant.
- Produto publicado é snapshot versionado; editar o rascunho não altera
  silenciosamente o snapshot já publicado.

## Estado real conhecido

Após o smoke de produção: 3 sellers, 2 memberships de owner, 3 produtos de
catálogo, 11 assets, 0 entitlements de buyer no momento da medição, 1 grant
`platform_superadmin` ativo e slugs `autonomia`/`hub2you` ativos. O produto de
referência Hub2You é `ia-na-pratica`; o produto pago de smoke é
`teste-checkout-r1`.

## Comandos e validação local

```bash
corepack pnpm install --frozen-lockfile
corepack pnpm dev
corepack pnpm test
corepack pnpm test:e2e
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
```

Para a validação de release, preservar o SHA efetivamente implantado e separar
claramente CI/local de smoke real. Nunca registrar secrets, tokens ou dados
pessoais de buyers na documentação.

## Documentos canônicos

- [Estado atual de produção](../PRODUCTION_CURRENT.md)
- [Runbook de release/rollback](../runbooks/PRODUCTION_RELEASE_APPSSELL.md)
- [Status](../STATUS.md) e [progresso](../PROGRESS.md)
- [Matriz de autorização](../architecture/AUTHORIZATION_MATRIX.md)
- [Modelo de dados](../architecture/DATA_MODEL.md)
- [Registro de decisões](../architecture/DECISION_REGISTER.md)
- [Issue #202 de documentação](https://github.com/autonom-ia/appsell/issues/202)

## Regras de operação

- Toda tarefa persistente segue Issue → Branch → PR → Project → review →
  aprovação → merge → deploy/rollback.
- Não fazer merge, deploy, alteração de banco, secrets, auth, billing, DNS ou
  dados reais sem aprovação explícita correspondente.
- Não usar demo para validar produção; fazer teste negativo cross-tenant.
- Não declarar sucesso por CI ou healthcheck isolado: separar CI, merge,
  deploy, migration, smoke e resultado do usuário.
- O Autonom.ia Agent Harness está descontinuado; não instalar, restaurar ou
  usar seus hooks/documentos como requisito.

## Pendências sem ampliar o escopo atual

- rotina recorrente de backup/PITR e restore real continuam sendo trabalho de
  infraestrutura separado;
- billing/Stripe live, repasse/Connect e políticas comerciais precisam de
  gate próprio;
- novas mudanças do editor, catálogo ou tenants devem partir da produção
  atual e ter regressão negativa antes de qualquer deploy.
