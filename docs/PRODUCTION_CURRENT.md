# Estado atual — Autonom.ia Sell

> Fonte operacional para retomar o projeto sem depender do histórico da conversa.
>
> Última verificação documentada: 2026-08-25. Esta página separa o que foi
> comprovado na produção do que continua sendo plano ou laboratório Local/CI.

## Release em produção

| Item | Estado |
|---|---|
| Domínio | `https://sell.autonomia.site` |
| Ambiente | VPS EasyPanel nativo, Docker Swarm |
| SHA implantado | `3ddd58b9d2efe192185f0708fd024b5ca278ed7e` |
| Imagens | `web:3ddd58b`, `api:3ddd58b`, `asset-worker:3ddd58b` |
| API health | `{"status":"ok","database":true,"storage":true,"pdf_sandbox":true}` |
| Web health | `{"status":"ok","redis":true,"api":true,"storage_upload":true}` |
| CI pós-merge | [run 32656218821](https://github.com/autonom-ia/appsell/actions/runs/32656218821) — success |
| PR | [#201](https://github.com/autonom-ia/appsell/pull/201) — merged |
| Entrega | [Issue #195](https://github.com/autonom-ia/appsell/issues/195) — 14/14 aceites, encerrada |

O release de 2026-08-25 aplicou as migrations `0034`, `0035` e `0036` e trocou
as imagens de web, api e asset-worker. O banco de produção está no head
`0036_lesson_material_links` com `app_private.schema_ready()` = `t`.

Antes desta entrega o estado real divergia desta página: produção rodava
`50b6301` com o banco em `0033_pdf_study_state`, não `2c8a3689`/`0032`.

Backup lógico pré-cutover:
`/home/claudete/backups/appsell/appsell_native_90_20260825T114340Z.dump`
(881 KB, 828 objetos, validado com `pg_restore --list`, permissão 600).
Rollback de imagem: tag `easypanel/sell-autonomia/api:50b6301` segue no host.

## Hierarquia de contas

```text
Plataforma
└── comercial@autonomia.site
    └── platform_superadmin
        ├── Tenant Autonom.ia
        │   └── rodrigo.victor@autonomia.site
        │       └── seller_owner
        └── Tenant Hub2You
            └── comercial@hub2you.ai
                └── seller_owner

Usuário comprador
└── identidade global autenticada
    └── buyer_entitlement por seller + produto + publicação
```

`platform_superadmin` é um grant global explícito. Ele não é convertido em
membership de seller e não dá acesso automático ao conteúdo privado do aluno.
`seller_owner` administra somente o tenant da sua membership. O comprador não
é membership administrativa; seu acesso nasce de um entitlement ativo.

## Produtos e jornada do seller

O editor segue a ordem:

1. **Produto** — identidade, slug, título e descrição.
2. **Currículo** — módulos, aulas e materiais.
3. **Oferta** — nome, resumo, preço e cobrança.
4. **Preview** — revisão privada do que será publicado.
5. **Publicar** — criação do snapshot versionado e escolha do acesso.

Publicar catálogo cria uma versão fixa do produto, currículo, aulas e materiais
verificados. Publicar não cobra, não cria compra e não cria entitlement.

O seller pode publicar com acesso gratuito. A ativação de vendas/checkout é uma
decisão separada na etapa **Publicar** e só pode ocorrer depois de existir um
catálogo publicado.

### Acesso gratuito

O link público tem o formato:

```text
/s/{seller_slug}/p/{product_slug}/access
```

O visitante é levado a cadastro/login e, depois da autenticação, retorna ao
produto. A API `grant_public_product_access` cria ou reaproveita de forma
idempotente o entitlement e o enrollment; o curso aparece em **Minha
biblioteca**. Não há checkout nem cobrança nesse modo.

### Acesso pago

Com a oferta ativa, o link exige checkout. O conteúdo não é liberado antes da
confirmação do pagamento/fulfillment. Tentar usar o grant gratuito em produto
pago falha com `P4222`; o entitlement só nasce pelo fluxo de checkout autorizado.

### Conteúdo e materiais

PDF/MP4 passa por upload, armazenamento, worker e verificação de segurança. Só
material pronto pode entrar em nova publicação. O nome físico do arquivo não é
apresentado ao aluno; o conteúdo é servido dentro da biblioteca, com autorização
por identidade, seller, produto, publicação, entitlement e asset.

## Regra de acesso resumida

| Ator | Pode | Não pode |
|---|---|---|
| `platform_superadmin` | listar tenants e administrar o catálogo dos tenants | virar seller member automaticamente ou receber conteúdo de aluno por implicação |
| `seller_owner` | editar, revisar e publicar o próprio tenant | ler/editar/publicar produto de outro tenant |
| `buyer` | acessar aulas/PDFs dos entitlements ativos na biblioteca | abrir editor, alterar catálogo ou publicar |
| visitante | ver vitrine pública e iniciar cadastro/login | ler conteúdo privado |

## Evidência final dos aceites

- Tenants `autonomia` e `hub2you` foram listados pelo superadmin.
- Edição transacional de produto foi exercitada nos dois tenants e revertida ao
  final do smoke test.
- `comercial@hub2you.ai` abriu `IA na prática` sem 404; currículo, oferta,
  preview e publicação carregaram.
- O owner Hub2You foi negado no produto Autonom.ia.
- O fluxo gratuito criou um entitlement, carregou biblioteca, player e PDF, e o
  replay retornou o mesmo entitlement com `created=false`.
- O produto pago exigiu checkout e permaneceu fora da biblioteca antes da
  confirmação.
- Estado preservado após o teste: 3 sellers, 2 memberships, 3 produtos, 11
  assets e 0 entitlements persistidos.
- O smoke foi executado dentro da API implantada com as imagens do SHA
  `2c8a3689`; toda escrita de teste ocorreu em transação revertida.

## Produção não usa demo

O banco e o domínio de produção não usam reset, seed ou personas demo. Os
scripts/documentos de demo continuam apenas para Local/CI descartável e devem
ser lidos com esse limite explícito. Nenhum reset demo deve ser executado contra
`appsell_native_90`.

## Próximos limites conhecidos

- Recebimentos/Stripe do seller continuam um fluxo separado da publicação e
  dependem da configuração financeira real do tenant.
- Staging separado, RPO/RTO formal e política final de refund/chargeback ainda
  são decisões operacionais pendentes.
- Qualquer mudança em produção deve criar Issue/PR, preservar backup, registrar
  o SHA e ter rollback definido antes do cutover.

## Documentos relacionados

- [Status do projeto](STATUS.md)
- [Progresso e histórico](PROGRESS.md)
- [Arquitetura](architecture/README.md)
- [Matriz de autorização](architecture/AUTHORIZATION_MATRIX.md)
- [Release, backup e rollback](runbooks/PRODUCTION_RELEASE_APPSSELL.md)
- [Memória do projeto](memory/MEMORY_PROJECT.md)
- [Memória da sessão](memory/MEMORY_SESSION.md)
