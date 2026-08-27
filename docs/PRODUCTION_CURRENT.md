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
| SHA do **web** | `bfeb14b` |
| SHA do **api** | `3524e2a` — subiu em 2026-08-27, ver abaixo |
| SHA do **asset-worker** | `3524e2a` — mesma imagem da api |
| SHA do **pdf-sandbox** | `f8738e4` |
| Imagens | `web:bfeb14b`, `api:3524e2a`, `asset-worker` em `api:3524e2a`, `pdf-sandbox:f8738e4` |
| Banco | head `0042_ready_candidate_media` |
| Web health | `{"status":"ok","redis":true,"api":true,"storage_upload":true}` |
| API health | `{"status":"ok","database":true,"storage":true,"pdf_sandbox":true}` |
| Épico | [#252](https://github.com/autonom-ia/appsell/issues/252) — 6/6 fechado |

## Release `3524e2a` — o documento chega ao fim do caminho (2026-08-27)

`app_private.register_asset_ready_candidate_v1_legacy` era a **única** função do
caminho de verificação que restringia formato:

```sql
or p_ready_media not in ('application/pdf','video/mp4')
  then raise exception 'invalid ready asset candidate' using errcode='22023'
```

Tudo antes dela já aceitava os nove formatos — o intent, o `confirm`, o
`finalize`, a constraint de `asset_blob`, a de `asset_storage_cleanup_job`. O
arquivo subia, passava no antivírus, era verificado em 0,1 s e morria na última
porta. **DOCX, XLSX, PPTX, TXT, MD, CSV e JSON nunca puderam ser registrados**,
desde que entraram no catálogo. E o worker engolia a exceção sem uma linha de
log: a tela dizia "a verificação está demorando mais que o esperado", que promete
que vai resolver sozinho.

A `0042` troca a lista fechada por `asset_media_extension(p_ready_media) is null`
— deriva do catálogo criado na `0039`, em vez de listar formato de novo. Os
quatro `except DBAPIError` mudos do worker passam a registrar sqlstate e a frase
do banco, com redação de sequência de 32+ caracteres.

Detalhe da entrega: [#291](https://github.com/autonom-ia/appsell/issues/291),
[PR #292](https://github.com/autonom-ia/appsell/pull/292).

### Como foi aplicado

| passo | comando |
|---|---|
| backup lógico | `pg_dump -Fc` → `appsell_native_90_pre0042_20260827T112847Z.dump` (922 entradas) |
| specs | `spec_{api,asset-worker,web,pdf-sandbox}_pre0042_20260827T112847Z.json` |
| migration | container descartável da `api:3799d86` com `migrations/` e `alembic.ini` montados do host |
| build | `docker build -f deploy/easypanel-native/Dockerfile.api --build-arg GIT_SHA=3524e2a` |
| deploy | `docker service update --image ...api:3524e2a` em `asset-worker`, depois `api` |

> **A imagem da `api` tem o binário do Alembic mas não tem as migrations.** Só
> `apps/api/src` entra no `Dockerfile.api`. Por isso a migration roda montando
> `/home/claudete/appsell-src-<sha>/apps/api/migrations` de leitura no
> contêiner, na rede `easypanel-sell-autonomia`. A senha do migrator é lida da
> spec do serviço `bootstrap-native-90` em tempo de execução; nunca fica escrita
> em disco.

### Validação executada em produção

- `alembic current` → `0042_ready_candidate_media`
- `schema_ready_base_0016()` e `_0017()` → `true`
- a lista fechada sumiu da definição viva; a linha derivada está lá
- os nove formatos do catálogo respondem extensão; `application/x-msdownload`
  responde `NULL` (continua recusado)
- worker em `3524e2a`, 0 reinícios, tmpfs de 3 GiB intacto, `pdftoppm` e
  `ffmpeg` presentes — conferido **de dentro** do contêiner
- `/health/ready` da api → `{"status":"ok","database":true,"storage":true,"pdf_sandbox":true}`
- `https://sell.autonomia.site/api/health/ready` → `{"status":"ok","redis":true,"api":true,"storage_upload":true}`

**A prova de verdade:** o `.md` que o vendedor tentou subir às 10:38 estava
parado em `verifying` com o lease do worker antigo. Um segundo depois de o lease
vencer, o worker novo pegou:

```text
11:38:47 [info] asset_worker_completed upload_id=2d14b869-…
```

E o banco: `upload=ready`, `asset_blob` com `magic_type=text/markdown`, 10.557
bytes, `scanner=clean`, `asset_record` criado, rascunho ativo na aula. Nenhuma
intervenção manual.

### Rollback deste release

```sh
ssh claudete 'bash /home/claudete/rollback_0042.sh'
```

Volta `api` e `asset-worker` para `3799d86` e roda `alembic downgrade -1`.
**Voltar fecha de novo a porta do documento** — o script avisa isso ao terminar.
O dump lógico pré-migration é a rede embaixo dessa rede.

### O que este release NÃO resolveu

- **PDF acima de 100 páginas continua sendo recusado.** O worker rasteriza até
  300 desde 26/08 e o teto antigo sobrevive em doze lugares do banco, incluindo
  a autorização de página do aluno. Uma versão desta PR alargava parte deles; o
  review pegou que estava pela metade — alargar parte só muda a porta em que o
  vendedor bate. Virou [#293](https://github.com/autonom-ia/appsell/issues/293).
- **O papel do material continua vindo errado.** O `.md` entrou como
  `content_role=course_content` com `download_allowed=false`, que não é o que um
  material de apoio deveria ser. É a Fase 2 do plano de conteúdo da aula: o
  seletor "Uso deste material" sai e o painel em que o vendedor clica passa a ser
  a resposta.

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

## API em produção — o corte de 2026-08-26

Até este dia a API rodava com `APPSELL_API_ENVIRONMENT=ci` em produção. Não era
detalhe de rótulo: `ci` desliga de uma vez **quatro** guardas fail-closed, entre
elas a que **exige** storage S3. Foi essa a causa raiz da capa do produto e do
material de aula não subirem — a API devolvia `503 cover_storage_unavailable`
porque `APPSELL_API_STORAGE_PROVIDER=disabled`, e ligar o storage era impossível
enquanto `pdf_sandbox_required` significasse "tem storage" (corrigido em #269).

A escada medida, com o env real e a imagem `api:e112c2c`, em contêiner
descartável `--network none`:

| # | configuração | resultado |
|---|---|---|
| 1 | `environment=ci` (como estava) | OK — storage desligado |
| 2 | `environment=production` | ❌ `local webhook secrets are forbidden outside local/CI` |
| 3 | + `WEBHOOK_ENV_KEYRING_ALLOWED=true` | ❌ `S3 storage is required outside local/CI` |
| 4 | + as 11 variáveis de storage | ❌ `a real malware scanner is required outside local/CI` |
| 5 | + `MALWARE_SCANNER_PROVIDER=clamav` + socket | ❌ `production database URL must require TLS` |
| 6 | + `ssl=require` na URL do banco | ✅ OK |

### O que foi aplicado

Sete variáveis **e um volume** — o volume é o ponto que exige decisão, não
carimbo:

1. imagem `easypanel/sell-autonomia/api:e112c2c`
2. `APPSELL_API_ENVIRONMENT=production`
3. `APPSELL_API_WEBHOOK_ENV_KEYRING_ALLOWED=true`
4. `APPSELL_API_MALWARE_SCANNER_PROVIDER=clamav`
5. `APPSELL_API_MALWARE_SCANNER_SOCKET=/run/appsell-clamav/clamd.sock`
6. as 11 variáveis `APPSELL_API_STORAGE_*`, copiadas do `asset-worker`
7. `ssl=require` em `APPSELL_API_DATABASE_URL`
8. mount `sell-autonomia_clamav_socket` → `/run/appsell-clamav`, **read-only**,
   espelhando o worker

Contagem final: **32 variáveis, 1 mount**. Antes eram 20 e 0.

> **Armadilha aritmética, de novo.** Das 11 variáveis de storage, uma
> (`APPSELL_API_STORAGE_PROVIDER`) **já existia**. `--env-add` sobrescreve, e
> `--env-rm` no rollback **apagaria** em vez de restaurar. Por isso o rollback
> faz `--env-add APPSELL_API_STORAGE_PROVIDER=disabled` e `--env-rm` só nas
> outras 10.

### O que isto significa em segurança

A API exposta passou a ter socket do ClamAV. Dois limites permanecem:

- **O sandbox de PDF continua fora da API.** Capa é imagem; não passa pelo
  parser. `pdf_sandbox_required` é `False` porque `verifies_uploads` só é
  verdadeiro em local/CI.
- **Material de aula continua indo para o worker.** `inline_asset_worker_enabled`
  é `environment in {local, ci}` → `False` em produção.

Só a **capa** verifica dentro da requisição, e esse código já era assim
(`apps/api/src/appsell_api/cover_routes.py:225`) — hoje ele apenas deixou de dar
503. Tirar a capa de dentro da API é a
[#268](https://github.com/autonom-ia/appsell/issues/268), e é trabalho de código:
o worker não tem caminho de capa nenhum.

### Validação executada

- boot limpo, sem erro nos logs; handshake TLS com o Postgres passou
- `/health/ready` → `{"status":"ok","database":true,"storage":true,"pdf_sandbox":true}`
  (esse `storage:true` já cobre `probe_availability`, `assert_runtime_controls` e
  `scanner.healthy()`)
- `PING` no `clamd.sock` de dentro do contêiner → `PONG`
- round-trip real na produção: presign → `PUT` no MinIO **HTTP 200** → `HEAD` →
  scan de imagem limpa `True` → scan do EICAR `False` → objeto de teste apagado
- `https://sell.autonomia.site` HTTP 200

### Rollback deste release

```sh
ssh claudete 'bash /home/claudete/rollback_api.sh'
```

Desfaz imagem, as sete variáveis, o mount e o `ssl=require`. Backup íntegro da
spec anterior em
`/home/claudete/backups/appsell/appsell_api_spec_20260826T173516Z.json`
(20 variáveis, 0 mounts). ETA ~40 s. Não há migration para reverter.

### Release `b09c690` — a moldura da capa (2026-08-26, fim do dia)

Só `web`. A primeira capa real subida depois da #270 saiu esticada: um banner de
1683×954 desenhava um quadro de **200×954**.

Causa: os atributos `width`/`height` do `<img>` viram presentational hints de
CSS. O `.cover-image` sobrescrevia a largura (`200px`) mas não declarava altura,
então o hint de `954px` sobrevivia — e com as duas dimensões definidas, o
`aspect-ratio: 16 / 9` é ignorado.

**Regra que fica:** quando a folha manda na largura de uma imagem com atributos,
precisa mandar na altura também. O `.draft-card-cover` passa os mesmos atributos
e não tem o defeito, porque já declara `height: 100%`.

Nenhum teste pegou porque o fixture do e2e usa uma capa de 64×64 — com o
defeito, 200×64 não chama atenção — e nenhuma asserção media o quadro
renderizado. O teste novo usa as dimensões reais e mede: falhava com `954`,
passa com `112.5`.

Rollback:

```sh
ssh claudete 'sudo docker service update --image easypanel/sell-autonomia/web:e112c2c sell-autonomia_web'
```

Issue [#272](https://github.com/autonom-ia/appsell/issues/272), PR
[#273](https://github.com/autonom-ia/appsell/pull/273).

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
