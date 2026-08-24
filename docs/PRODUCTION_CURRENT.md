# Estado atual — Autonom.ia Sell

> Fonte operacional para retomar o projeto sem depender do histórico da conversa.
>
> Última verificação documentada: 2026-08-24. Esta página separa o que foi
> comprovado na produção do que continua sendo plano ou laboratório Local/CI.

## Release em produção

| Item | Estado |
|---|---|
| Domínio | `https://sell.autonomia.site` |
| Ambiente | VPS EasyPanel nativo, Docker Swarm |
| Release web | `1d2ea63119c9124c7d5a4960aa778c3f613f490f` |
| Release API/worker | `50b6301cfeb6afca696391d7ba8832642bde79e1` |
| Imagens | `web:1d2ea63`, `api:50b6301`, `asset-worker:50b6301` |
| Migration head | `0033_pdf_study_state` |
| Readiness público | `status=ok`, Redis, API e storage saudáveis |
| CI pós-merge atual | [run 32768382697](https://github.com/autonom-ia/appsell/actions/runs/32768382697), success |
| PRs do leitor | [#211](https://github.com/autonom-ia/appsell/pull/211), [#215](https://github.com/autonom-ia/appsell/pull/215) a [#219](https://github.com/autonom-ia/appsell/pull/219), [#223](https://github.com/autonom-ia/appsell/pull/223) e [#225](https://github.com/autonom-ia/appsell/pull/225) |
| Aceite mais recente | [Issue #224](https://github.com/autonom-ia/appsell/issues/224), validada pelo Rodrigo na sessão real |

A API e o asset-worker receberam a persistência de estudo no release `50b6301`.
O hotfix `1d2ea631` alterou somente o web; banco, migration, API, worker, auth,
billing e dados permaneceram intactos nesse último cutover.

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

### Leitor PDF protegido

- O original continua privado; o aluno recebe páginas rasterizadas sob
  autorização e cache controlado, com carregamento antecipado limitado.
- Links do índice navegam para a página correta e vídeos vinculados abrem em um
  player dentro da plataforma.
- Favoritos e marcações vetoriais são privados, persistentes e editáveis. A
  ferramenta Mão arrasta o documento com mouse; Editar marcação move, recolore,
  muda espessura ou exclui uma anotação existente.
- Toque com um dedo, pinça com dois dedos, zoom, retomada de página e cache da
  sessão permanecem disponíveis no mobile.
- A identificação fica em rodapé discreto fora do conteúdo. A plataforma reduz
  cópia casual, mas não promete bloquear captura de tela do sistema operacional.

## Regra de acesso resumida

| Ator | Pode | Não pode |
|---|---|---|
| `platform_superadmin` | listar tenants e administrar o catálogo dos tenants | virar seller member automaticamente ou receber conteúdo de aluno por implicação |
| `seller_owner` | editar, revisar e publicar o próprio tenant | ler/editar/publicar produto de outro tenant |
| `buyer` | acessar aulas/PDFs dos entitlements ativos na biblioteca | abrir editor, alterar catálogo ou publicar |
| visitante | ver vitrine pública e iniciar cadastro/login | ler conteúdo privado |

## Evidência da base multitenant

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

## Evidência do leitor e aceite do usuário

- O reader premium, links, vídeo interno, carregamento sob demanda, cache,
  anotações e favoritos foram integrados em PRs separados com CI verde.
- O hotfix da Mão e de Editar marcação foi mergeado no PR #225, SHA
  `1d2ea631`, e implantado somente no web.
- O container do web convergiu saudável; readiness público ficou verde e os
  logs recentes não apresentaram `ERROR`, `Traceback`, `FATAL` ou `panic`.
- Rodrigo validou Mão e Editar marcação na sessão autenticada real após o
  deploy, encerrando a Issue #224.
- Backup pré-deploy validado:
  `/home/claudete/backups/appsell/pre-web-1d2ea631.dump`, SHA-256
  `2a800ceea2605c3cf9b59b7a7d436d03b9c629cacf34365ae1384cbaa31dfd11`.
- Rollback do último cutover: restaurar somente o web para
  `easypanel/sell-autonomia/web:50b6301`.

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
