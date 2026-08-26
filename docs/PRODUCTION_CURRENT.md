# Estado atual — Autonom.ia Sell

> Fonte operacional para retomar o projeto sem depender do histórico da conversa.
>
> Última verificação documentada: 2026-08-26. Esta página separa o que foi
> comprovado na produção do que continua sendo plano ou laboratório Local/CI.

## Release em produção

> Última verificação: **2026-08-26**, lendo o host — não a memória desta página.
> Esta página já divergiu da realidade duas vezes; o que vale é o label
> `org.opencontainers.image.revision` da imagem em execução.

| Item | Estado |
|---|---|
| Domínio | `https://sell.autonomia.site` |
| Ambiente | VPS EasyPanel nativo, Docker Swarm |
| SHA do **web** | `b2a5a6e02edf777ac59e2c2128cb3f0db95e2dc6` (`b2a5a6e`) |
| SHA do **api** e do **asset-worker** | `4cbdcf7` — **não se moveram** |
| Imagens | `web:b2a5a6e`, `api:4cbdcf7`, `asset-worker` em `api:4cbdcf7` |
| Banco | head `0038_player_hides_materials` — **sem migration neste release** |
| Web health | `{"status":"ok","redis":true,"api":true,"storage_upload":true}` |
| Épico | [#252](https://github.com/autonom-ia/appsell/issues/252) — 6/6 fechado |

O release de 2026-08-26 (`b2a5a6e`) entregou o épico de interface #252 inteiro:
campos numéricos que recusam o inválido (#253), nenhum botão desligado em
silêncio (#254), vocabulário do vendedor (#255, que fechou o A4.5 da #232),
turmas (#256), cartão e entrada do catálogo (#257) e editor por etapas (#258).

**Só o serviço `web` mudou.** O diff de `4cbdcf7..b2a5a6e` não toca `apps/api`
nem `migrations`: são 13 arquivos em `app/`, 4 em `lib/` e 14 em `tests/`.
Por isso `api` e `asset-worker` seguem em `4cbdcf7` e o banco não foi tocado.

### Rollback deste release

A imagem anterior do web foi preservada **antes** do build, com o SHA que ela
carregava:

```sh
ssh claudete 'sudo docker service update --image easypanel/sell-autonomia/web:4cbdcf7 --force sell-autonomia_web'
```

ETA ~40 s. Não há migration para reverter.

> **Armadilha registrada:** os serviços `web` e `api` rodavam a imagem **sem
> tag** (`:latest`). Sem marcar a imagem em uso antes de reconstruir, o build
> sobrescreve `:latest` e **não sobra rollback**. Marcar primeiro, sempre.

### Releases anteriores

O release de 2026-08-25 aplicou as migrations `0034`, `0035` e `0036`. Depois
vieram `50f5849` e `adc3fa4` (sem migration), e em 2026-08-26 as migrations
`0037` (capa do produto) e `0038` (materiais do player) sobre `4cbdcf7`.

Backups lógicos em `/home/claudete/backups/appsell/`, o mais recente
`appsell_native_90_pre0038_20260826T071544Z.dump`. Tags de rollback de api no
host: `4cbdcf7`, `16112c5`, `adc3fa4`, `50f5849`, `3ddd58b`.

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

1. **Produto** — identidade, endereço, título e descrição.
2. **Conteúdo** — módulos, aulas e materiais.
3. **Preço** — nome no checkout, descrição, valor e cobrança.
4. **Distribuição** — publicação da versão fixa, modo de acesso e turmas.

Os rótulos mudaram na #255 e na #258; os identificadores de área na URL **não**
(`?section=produto|curriculo|oferta|publicar`), porque endereço em circulação é
contrato. A etapa "Preview" deixou de existir na Onda 3 — `?section=preview`
continua abrindo em Produto, sem erro.

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
