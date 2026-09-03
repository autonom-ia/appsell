# Estado atual — Autonom.ia Sell

> Fonte operacional para retomar o projeto sem depender do histórico da conversa.
>
> Última verificação documentada: **2026-09-03**. Esta página separa o que foi
> comprovado na produção do que continua sendo plano ou laboratório Local/CI.

## Release em produção

> Última verificação: **2026-09-03**, lendo o host — não a memória desta página.
> Esta página já divergiu da realidade três vezes; o que vale é o que
> `docker service ls` responde.

| Item | Estado |
|---|---|
| Domínio | `https://sell.autonomia.site` |
| Ambiente | VPS EasyPanel nativo, Docker Swarm |
| SHA do **api** | `8a0eca0` — Configurações da loja, logo, promessa e vitrine com marca (#352) |
| SHA do **asset-worker** | `d5e00ec` — mesma imagem da api, um SHA atrás |
| SHA do **pdf-sandbox** | `d5e00ec` — mesma imagem da api, com outra tag |
| SHA do **web** | `8a0eca0` — Configurações da loja e mini-LP do produto (#352); worker e sandbox seguem em `d5e00ec` |
| Banco | head **`0052_configuracoes_da_loja`**, `schema_ready() = true` |

**Release de 2026-09-03, `web` e `api`: `8a0eca0`, com a migration `0052`.** A
loja ganha marca própria (nome, frase, sobre, cor, logo), a página do produto
vira página de vendas, Recebimentos vão para dentro de Configurações e só
dono e admin configuram (#352, PR #383). Detalhe na seção
[Release `8a0eca0`](#release-8a0eca0--configurações-da-loja-e-página-de-vendas-2026-09-03).

**Release de 2026-09-02, `api` e `web`: `803fa82`, com a migration `0051`.** Toda
identidade real ganha `/settings/account`: dados, verificação, senha via Identity,
acessos, exportação dos próprios dados e pedido de exclusão (#351, PR #380).
Detalhe na seção [Release `803fa82`](#release-803fa82--minha-conta-do-comprador-2026-09-02).

**Release de 2026-09-02, só a `api`: `374c10b`, com a migration `0050`.** Quem
entra por link de turma passa a receber o "acesso liberado", e a tela de quem
não confirmou o e-mail deixa de oferecer o caminho que não funciona (#357,
#301, PR #378). Detalhe na seção
[Release `374c10b`](#release-374c10b--o-link-de-turma-avisa-e-o-e-mail-não-confirmado-diz-o-caminho-2026-09-02).

**Release de 2026-09-02, só a `api`: `70e6cb6`, com a migration `0049`.** A
caixa de saída de e-mail nasce na transação que libera o acesso, e o worker
que envia roda dentro da API (#356, PR #374). Detalhe na seção
[Release `70e6cb6`](#release-70e6cb6--a-compra-passa-a-avisar-quem-comprou-2026-09-02).

**Release de 2026-09-02, só o `web`: `9655a13`.** A passagem entre os atos do
hero deixou o scrub e passou a tocar no tempo (Decisão 018, #369, PR #371). O
caminho foi o do runbook: spec e nomes das 52 variáveis salvos em `/tmp` do
host, imagem construída com `--build-arg GIT_SHA`, `service update` com
`stop-first`, variáveis iguais por nome, páginas 200 e o CSS servido contendo
`align-content:safe center` como prova do bundle novo. Rollback: `web:581a592`,
ainda no host. O health do `web` responde em `/api/health/live` e
`/api/health/ready`; não existe `/api/healthz` no Next.

**Os quatro andaram no mesmo SHA por algumas horas, em `d5e00ec`.** O release do
vídeo longo obrigou o `pdf-sandbox` a subir junto, porque o modo que recusa
recodificar mora nele. No mesmo dia o `web` seguiu sozinho para `9ebb93f`, com a
página pública nova: nada além do Next mudou, então api, worker e sandbox
continuam em `d5e00ec`.

Historicamente os serviços não andavam no mesmo SHA, e isso era intencional: o
release de 31/08 tocou só a API e o worker. A partir de 01/09 eles convergiram,
porque o vídeo longo mudou os quatro. A armadilha do `--update-order
start-first` no `pdf-sandbox` continua valendo, e ele **sempre** sobe com
`stop-first` e `--no-healthcheck`.

### O log da API

Desde `12d8763` a API grava log estruturado: uma linha JSON por requisição, com
o template da rota, duração e identificador de correlação. Antes eram **22
linhas em 24 horas**, vinte delas ciclo de vida do uvicorn. O contrato, o que o
redator apaga e as armadilhas estão em [TROUBLESHOOTING](TROUBLESHOOTING.md).

**Validar a API pelo `https://sell.autonomia.site/api/health/ready` não prova
nada sobre a API.** Esse caminho é o BFF Next: o healthz dele devolve `redis` e
`storage_upload`. O da API Python devolve `database` e `pdf_sandbox`, e só é
alcançável de dentro do container:

```sh
CID=$(sudo docker ps --filter name=sell-autonomia_api --format '{{.ID}}' | head -1)
sudo docker exec $CID python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=8).read().decode())"
```
| API health | `{"status":"ok","database":true,"storage":true,"pdf_sandbox":true}` |

### Recursos dos serviços, como estão no host

| Serviço | CPU | Memória | `/tmp` |
|---|---|---|---|
| `api` | 0,75 | 768 MiB | — |
| `pdf-sandbox` | **4,0** | **4 GiB** | 2 GiB |
| `asset-worker` | 1,0 | **4 GiB** | 3 GiB |
| `web` | 0,75 | 768 MiB | — |

**Duas divergências entre o script e o host**, encontradas no deploy de 31/08 e
que continuam valendo como alerta:

- `ensure-asset-worker.sh` declarava 1 GiB de memória enquanto o host tinha 4.
  Ao atualizar o worker eu apliquei o valor do script e **reduzi produção sem
  querer**; devolvi em seguida. Antes de passar `--limit-memory`, leia o valor
  atual.
- O `/tmp` do worker no host já era 3 GiB, não os 384 MiB do script.

E `ensure-asset-worker.sh` **aborta quando o serviço já existe** — ele não
atualiza o worker sozinho.

## Limites de conteúdo em produção

| O quê | Limite | Onde vive |
|---|---|---|
| Páginas por PDF | **300** | 9 lugares: Python, TypeScript, 6 restrições, 3 funções, `schema_ready()` |
| Tamanho do PDF | 100 MiB | catálogo de mídia (`asset_media`) |
| Página rasterizada | 8 MiB | restrição do banco |
| Pacote rasterizado | 384 MiB | Python + 2 restrições + gatilho + função |
| Vídeo enviado | 100 MiB | catálogo |
| Vídeo citado no PDF | 512 MiB | `pdf_link_video` — **incoerente com o catálogo**, ver abaixo |
| Duração de vídeo | 4 h | `video_remux` — **promete o que não entrega**, ver abaixo |
| Capa do produto | 5 MiB, 64–4000 px | restrição do banco |

### O que foi medido, e não estimado

Com o playbook real do vendedor, dentro do serviço isolado em produção:

```
rasterizar 1 página a 150 dpi    0,73 s   pico de 83 MiB — constante
página rasterizada               243 KiB média, 642 KiB a maior
300 páginas, 1 núcleo, 1 proc    208 s
300 páginas, 1 núcleo, 4 procs   207 s   ← paralelizar sem núcleo não adianta
300 páginas, 4 núcleos, 4 procs   89 s
subir 300 páginas ao storage      9,5 s  (32 ms cada)

converter vídeo 1080p            66 s por minuto de vídeo  ← mais lento que o vídeo
trocar container (MP4/H.264)     0,155 s por minuto        ← 400× mais rápido
```

A memória **não cresce com o documento** — o `pdftoppm` desenha uma página por
vez. O que cresce é o tempo, e é por isso que a rasterização corre em faixas
quando há núcleo: `raster_workers()` lê a cota do cgroup e decide. `os.cpu_count()`
não serve, porque dentro do contêiner ele conta a máquina.

Orçamento do lease do worker, medido de ponta a ponta com 300 páginas:

```
download 0,2 s · antivírus 3,0 s · raster+pacote 208 s · upload 9,5 s · banco 5 s
= 226 s de 600 s   (folga 2,7×)
```

### Dois limites que ainda não fecham

**Vídeo tem dois tetos.** O enviado pelo vendedor vale 100 MiB, pelo catálogo; o
citado dentro do PDF vale 512, número que nasceu no módulo de vídeo sem olhar o
catálogo. Unificar exige decidir o valor, e o valor certo depende do item
seguinte.

**A duração de 4 h é ficção.** Recodificar 4 h leva 4,4 h e o prazo da conversão
é de 1 h: o vídeo é aceito no envio e nunca fica pronto. Uma aula de 60 min em
1080p decente ocupa 1,3–2,2 GiB, e subir o teto para isso exige **envio em
pedaços** — hoje o upload é um PUT único com 5 min de prazo, o que exigiria 74
Mbit/s constantes do vendedor.

O ganho real para quem publica não é o número desses tetos: é **exportar em
MP4/H.264**, que troca só o container e entra em segundos.

### Por que 300 e não 1000

Não é memória nem disco. É que o serviço isolado atende **um arquivo por vez**, e
enquanto rasteriza ninguém consegue ler os links de PDF nenhum — quem abrir um
guia nesse intervalo o vê sem botões ([#320](https://github.com/autonom-ia/appsell/issues/320)).
Com 4 núcleos, 300 páginas são 89 s de janela.

Mil páginas exigem, além da #320: virtualizar o leitor (hoje cria um nó por
página), paralelizar o upload das páginas, e refazer gatilhos que recontam todas
as páginas a cada inserção. Cinco mil é outra geração do pipeline.

## Release `8a0eca0` — Configurações da loja e página de vendas (2026-09-03)

`web` e `api`, mais a migration `0052`. Fecha a A2 (#352) do trilho de
configurações por papel. Merge por rebase da PR #383 (5 commits; review
adversarial em 3 rodadas, APROVADO na terceira).

### O que mudou para quem usa

- **Configurações → Loja** (`/settings/store`): nome, frase de apresentação,
  "sobre quem ensina", cor de destaque (recusada se não contrastar 4,5:1 com
  texto branco), logo (mesmo verificador da capa), e o cartão "Seus links" com
  o endereço da loja para copiar. O endereço público é fixo.
- **Configurações → Recebimentos** (`/settings/payouts`): a tela de ontem, no
  lugar novo. `/payouts` responde 308.
- Menu "Configurações" só para dono e admin; editor e suporte não veem e a API
  recusa (`42501`).
- **Vitrine e páginas do produto/acesso** com a marca do vendedor: logo ou
  iniciais, frase, cor nos botões; capa do produto passa a ser pública; a
  página do produto ganha promessa, "o que você vai receber" (gerado da versão
  publicada: títulos de aula e contagem de materiais), "sobre quem ensina" e o
  botão repetido fixo no celular.
- **Promessa** no editor do produto (área Produto), salva à parte do rascunho.
- Painel: aviso quando há produto pago publicado e a conta de recebimentos não
  está pronta, com link para Recebimentos.

### Ordem de deploy: web → api → migration (o inverso da 0051)

Capa e conteúdo já existem em produtos publicados, então o banco novo devolve
campos que o parser do web antigo (chaves exatas) recusaria. O web novo aceita
as duas formas; a API nova troca chave de objeto por URL antes de responder e
lê a promessa via `to_jsonb(product)->>'headline'`, que é nulo enquanto a
coluna não existe. O review pegou a ordem invertida na primeira versão da PR.

### Como foi aplicado

```bash
D=/tmp/sell-release-8a0eca0-<ts>                    # specs api/web, nomes das vars, pg_dump -Fc
# fonte via git archive → /home/claudete/appsell-src-8a0eca0
# imagens: deploy/easypanel-native/Dockerfile.web e Dockerfile.api, --build-arg GIT_SHA=8a0eca0
docker service update --image easypanel/sell-autonomia/web:8a0eca0 --update-order stop-first sell-autonomia_web   # 1
docker service update --image easypanel/sell-autonomia/api:8a0eca0 --update-order stop-first sell-autonomia_api   # 2
docker run --rm --network easypanel -e APPSELL_MIGRATOR_DATABASE_URL=<montada no host> \
  -v /home/claudete/appsell-src-8a0eca0/apps/api:/mig -w /mig \
  easypanel/sell-autonomia/api:8a0eca0 alembic upgrade head                                                       # 3
```

### Validação executada em produção

| O quê | Resultado |
|---|---|
| web | convergiu em `8a0eca0`; 53 variáveis iguais nome a nome; bundle novo provado (`storefront-brand`, rodapé novo); `/payouts` → 308 `/settings/payouts` |
| api | convergiu em `8a0eca0`; 39 variáveis iguais nome a nome; `ready` ok; zero erros no log; vitrine 200 contra o banco ainda em `0051` |
| migration | head `0052`, `schema_ready()=true`; 10 funções com dono `appsell_owner`; as 8 do runtime com `secdef` e EXECUTE, as 2 auxiliares sem; 11 colunas na loja, `headline` no produto, `seller_logo_upload` |
| vitrine | `/s/hub2you` devolve o resumo do conteúdo dos dois produtos e a capa do "IA na prática"; página do produto com "O que você vai receber", botão fixo e marca |
| público | `/` `/api/health/ready` `/s/*` 200 · `/settings/store` anônimo 307 · `GET /api/sellers/<id>/settings` anônimo 401 |

### O que não foi validado

A tela `/settings/store` com sessão de dono (salvar nome/cor, subir logo real
contra o S3). É o que o Rodrigo confere primeiro.

### Achado alheio a este release

`APPSELL_CHECKOUT_PROVIDER=financial_checkout` está no `web` desde algum
momento entre o release de ontem (não estava no backup do `803fa82`) e o
backup deste. Com ele, todo produto pago passa por `isProductCheckoutAvailable`
e a página oferece "Entre para comprar" — inclusive o da Hub2you, que não tem
conta de recebimentos. Este release não tocou nas variáveis (iguais nome a
nome). O que isso significa está na #382.

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/web:803fa82 --update-order stop-first sell-autonomia_web
docker service update --image easypanel/sell-autonomia/api:803fa82 --update-order stop-first sell-autonomia_api
# só se precisar tirar as colunas e funções: alembic downgrade -1 (apaga logos, cor, frase, sobre, promessa)
```

Atenção: com o banco em `0052`, o web `803fa82` recusa a vitrine de lojas
com produto publicado (parser com chaves exatas). Rollback do web exige
rollback do banco antes, ou o inverso da ordem de subida.

## Release `803fa82` — Minha conta do comprador (2026-09-02)

`api` e `web`, mais a migration `0051`. Fecha a A1 (#351) do trilho de
configurações por papel. Merge por rebase da PR #380 (10 commits, 5 rodadas
de review adversarial — as duas primeiras acharam defeito de escopo; as três
seguintes refinaram o teto de exportação que a própria PR introduziu).

### O que mudou para quem usa

- **Minha conta** (`/settings/account`) no menu de toda identidade real —
  aluno, vendedor ou quem ainda não tem loja. Persona demo de aluno não vê.
- Mostra e-mail, status de verificação, nome e lojas; "Redefinir minha senha"
  aponta para o `/forgot-password` do Identity (irmão do login). Passkeys e
  segundo fator não têm tela — a página diz isso e manda escrever.
- **Meus acessos**: o mesmo contrato da biblioteca (`/api/me/products`).
- **Exportar meus dados**: arquivo `meus-dados-autonomia-sell-<data>.json`
  com identidade, memberships, acessos, compras, progresso, marcadores,
  anotações (com os traços), e-mails enviados e histórico de ações. Nunca:
  `identity_sub`, issuer, hashes, `metadata` de auditoria, ids internos.
  Três por dez minutos **por pessoa**; teto de 8 MiB recusado pelo banco
  antes de agregar (`413` na tela, atendimento manual pelo runbook).
- **Pedir exclusão da conta**: registra o pedido em `audit_event` (Decisão
  019). Não apaga nada. Fica aberto até a operação registrar
  `account.deletion.completed`; enquanto aberto, a tela mostra a data e
  esconde o botão. Atendimento: [PEDIDO_DE_EXCLUSAO_DE_CONTA.md](runbooks/PEDIDO_DE_EXCLUSAO_DE_CONTA.md).

### Por que teve migration, e por que ela foi ANTES da API

A issue pedia "sem migration", mas a política `audit_member_insert` só deixa o
runtime escrever auditoria de quem tem membership numa loja — comprador puro
não grava nada. A `0051` cria três funções SECURITY DEFINER e nenhuma tabela
(`export_account_data`, `request_account_deletion`, `read_account_status`),
mais três índices por comprador. Função nova sem chamador é inofensiva; a API
nova sem as funções responderia 503 — por isso a migration subiu primeiro, o
inverso da `0050`.

### Como foi aplicado

```bash
D=/tmp/sell-release-803fa82-20260902-201936      # specs api/web, nomes das vars, pg_dump -Fc
# fonte via git archive → /home/claudete/appsell-src-803fa82; imagens api e web com --build-arg GIT_SHA=803fa82
docker run --rm --network easypanel -e APPSELL_MIGRATOR_DATABASE_URL=<montada no host> \
  -v /home/claudete/appsell-src-803fa82/apps/api:/mig -w /mig \
  easypanel/sell-autonomia/api:803fa82 alembic upgrade head          # 1. migration
docker service update --image easypanel/sell-autonomia/api:803fa82 \
  --update-order stop-first sell-autonomia_api                        # 2. api
docker service update --image easypanel/sell-autonomia/web:803fa82 \
  --update-order stop-first sell-autonomia_web                        # 3. web
```

### Validação executada em produção

| O quê | Resultado |
|---|---|
| migration | head `0051`, `schema_ready()=true`, 3 funções com dono `appsell_owner` e `secdef=true`, 3 índices |
| api | convergiu em `803fa82`; graça `5m30s`; 39 variáveis iguais nome a nome; `ready` `{"database":true,"storage":true,"pdf_sandbox":true}`; zero erros no log |
| web | convergiu em `803fa82`; 52 variáveis iguais nome a nome |
| público | `/` 200 · `/api/health/ready` 200 · `/privacidade` 200 |
| conta | `/settings/account` anônimo → 307 `/` · `GET /api/me/account` anônimo → 401 · `POST …/export` anônimo → 403 |

### O que não foi validado

A tela com sessão real (render, exportação e pedido) — o deploy não tem
navegador logado. Uma exportação real aparece em
`select action,created_at from audit_event where action in ('account.data.exported','account.deletion.requested') order by created_at desc`.

### O que fica de fora, declarado

A exclusão de verdade começa no Identity (Cognito): enquanto a pessoa
conseguir entrar, `sync_identity` recria e-mail e nome. Tombstone por
`issuer + sub` e revogação administrativa de sessão são follow-ups sem issue.
Retenção por campo é a D-018, pendente.

### O CI reprovou uma vez, e o motivo vale lembrar

`permission denied for function read_account_status` no `downgrade`: as três
funções nasceram com dono = migrador porque o `set local role` do `env.py` não
vale na hora do `create`. Sete migrations já fazem `alter function … owner to
appsell_owner`; a `0051` esqueceu. Reproduzido com
`scripts/ci/exercita_migrations.sh` em banco limpo — o `alembic downgrade` no
banco de testes local roda como outro papel e não vê o defeito.

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/web:9655a13 --update-order stop-first sell-autonomia_web
docker service update --image easypanel/sell-autonomia/api:374c10b --update-order stop-first sell-autonomia_api
# só se precisar tirar as funções: alembic downgrade -1 (os audits ficam; os índices saem)
```

A `0051` é compatível com a imagem anterior (nada chama as funções). Dump
íntegro em `$D/appsell_native_90_antes.dump`.

## Release `374c10b` — o link de turma avisa, e o e-mail não confirmado diz o caminho (2026-09-02)

Só a `api`, mais a migration `0050`. Fecha o escopo combinado da #357 (2A + 3)
e a #301.

### O que mudou para quem usa

- Quem resgata um **link de turma** recebe o "acesso liberado" — uma vez por
  pessoa, e também quando o link **devolve** um acesso revogado, expirado ou
  ainda não iniciado. Quem já tinha o curso consome a vaga e **não** recebe:
  o link não liberou nada.
- Quem cria conta e não confirma o e-mail via a tela dizer **"Confirme seu
  e-mail"**, com o caminho de volta, em vez de "Entre novamente" — que
  produzia exatamente a mesma recusa. Sem botão de reenviar: quem envia a
  confirmação é o Identity, e a Sell não tem API para isso.

### A ordem do deploy inverteu, e isso é a regra daqui em diante

**API antes da migration.** O worker novo com o banco antigo é inofensivo
(nenhuma linha do tipo novo existe); na ordem contrária, o worker em produção
receberia um tipo que não conhece. A `0050` também blinda esse caso no banco:
`kind_unknown` nunca vira `dead_letter`, devolve a tentativa e ganha recuo de
dez minutos — o que protege igualmente um **rollback** para a imagem anterior.

### Como foi aplicado

```bash
D=/tmp/sell-release-374c10b-20260902-181204          # specs, imagem anterior, pg_dump -Fc
docker service update --image easypanel/sell-autonomia/api:374c10b \
  --update-order stop-first sell-autonomia_api        # 1. serviço
docker run --rm --network easypanel -e APPSELL_MIGRATOR_DATABASE_URL=<montada no host> \
  -v /home/claudete/appsell-src-374c10b/apps/api:/mig -w /mig \
  easypanel/sell-autonomia/api:374c10b alembic upgrade head   # 2. migration
```

### Validação executada em produção

| O quê | Resultado |
|---|---|
| serviço | convergiu em `api:374c10b`; graça `5m30s` preservada |
| variáveis | nome a nome contra a spec salva: **0 perdidas, 0 novas** |
| migration com a API nova no ar | head `0050`, `schema_ready()=true`, trava de idempotência e recuo de 10 min presentes na função, `kind` aceita `access_link_redeemed` |
| `ready` da API Python | `200` antes e depois da migration |
| worker | `email_worker_identity` ativo; nenhum `email_worker_disabled`; zero erros no log |
| público | `/` 200 · `/api/health/ready` 200 · `/privacidade` 200 |

### O que não foi validado

**Um resgate real gerando o e-mail** — depende de alguém entrar por um link de
turma. A fila mostra: `select kind,status,error_code,message_id,sent_at from
app_private.email_outbox order by created_at desc limit 20`.

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/api:70e6cb6 \
  --update-order stop-first sell-autonomia_api
```

A `0050` **fica**: ela é compatível com a imagem anterior — o worker antigo
devolve o tipo que não conhece para a fila, sem matar nada — e descê-la exige
`alembic downgrade -1`, que preserva as linhas já enviadas. Dump íntegro em
`$D/appsell_native_90_antes.dump`.

## Release `70e6cb6` — a compra passa a avisar quem comprou (2026-09-02)

Só a `api` mudou, mais a migration `0049`. `web`, `asset-worker` e
`pdf-sandbox` ficaram como estavam. Fecha a #356 (B1 do épico #349).

### O que mudou para quem usa

Uma compra aprovada passa a gerar **um** e-mail de "acesso liberado" para o
comprador — um só, mesmo com o webhook reprocessado, e nenhum em reembolso.
Ele sai de `sell@autonomia.site`, com a moldura da página pública, e vai para o
e-mail da **conta** (o que a pessoa usa para entrar), não para o digitado no
checkout quando os dois diferem.

### Como é feito, em uma frase por peça

- `app_private.email_outbox` é escrita **dentro** de `apply_checkout_fulfillment`,
  logo após o `buyer_entitlement`: grant e intenção de enviar são a mesma
  transação. Um SMTP fora do ar não bloqueia o acesso; a linha espera.
- O worker (`email_worker.py`) vive no processo da API, como o de fulfillment:
  claim com lease de 300 s e fence, um item por vez, `smtplib` em thread,
  `complete`/`fail` por função SQL que exige o lease vivo, auditoria em
  `audit_event` sem corpo.
- Sem `SMTP_*_TRANSACIONAL` o worker **não inicia** e registra
  `email_worker_disabled` com o motivo; nunca cai no remetente comercial.
- Falha de canal (credencial, host, STARTTLS/AUTH ausentes) pausa o worker e
  não consome tentativa do item; só falha do item vira `dead_letter`.
- A moldura Python é uma porta da TS, com goldens comparados byte a byte nos
  dois lados.

### Como foi aplicado

```bash
# 1. backups
D=/tmp/sell-release-70e6cb6-20260902-114346
for s in api web asset-worker; do docker service inspect sell-autonomia_$s > $D/spec_${s}_antes.json; done
docker exec <postgres-native-90> sh -c 'pg_dump -U "$POSTGRES_USER" -d appsell_native_90 -Fc' > $D/appsell_native_90_antes.dump

# 2. fonte e imagem
git archive --format=tar origin/main | ssh <host> 'tar -x -C /home/claudete/appsell-src-70e6cb6'
docker build -f deploy/easypanel-native/Dockerfile.api --build-arg GIT_SHA=70e6cb6 -t easypanel/sell-autonomia/api:70e6cb6 .

# 3. migration, com a API antiga no ar (compatível: só deixa linhas pendentes)
docker run --rm --network easypanel -e APPSELL_MIGRATOR_DATABASE_URL=<montada no host a partir do env do bootstrap> \
  -v /home/claudete/appsell-src-70e6cb6/apps/api:/mig -w /mig easypanel/sell-autonomia/api:581a592 alembic upgrade head

# 4. api: imagem, graça e as cinco variáveis numa atualização só
docker service update --image easypanel/sell-autonomia/api:70e6cb6 --stop-grace-period 330s \
  --env-add SMTP_HOST_TRANSACIONAL=... --env-add SMTP_PORT_TRANSACIONAL=... --env-add SMTP_SECURE_TRANSACIONAL=... \
  --env-add SMTP_USER_TRANSACIONAL=sell@autonomia.site --env-add SMTP_PASS_TRANSACIONAL=... \
  --update-order stop-first sell-autonomia_api
```

Os valores do SMTP saíram do arquivo local para variáveis de shell e foram
entregues pelo stdin do ssh; nunca passaram por argumento nem por arquivo no
host.

### O período de graça é parte do contrato

`--stop-grace-period 330s` (era `10s`). O worker espera o lease inteiro no
shutdown para um envio em curso terminar e ser registrado; com 10 s o Swarm
mataria o processo entre o `send` aceito e o `complete`, e o comprador
receberia duas vezes na tentativa seguinte. Conferir em todo release da api:

```sh
docker service inspect sell-autonomia_api --format '{{.Spec.TaskTemplate.ContainerSpec.StopGracePeriod}}'
```

### Validação executada em produção

| O quê | Resultado |
|---|---|
| migration com a API antiga no ar | head `0049`, `schema_ready()=true`, 4 funções novas, função de fulfillment reescrita (âncora 1×); API antiga seguiu `ready=200` |
| serviço | convergiu; `api:70e6cb6`, graça `5m30s`; task anterior `581a592` em Shutdown |
| variáveis | nome a nome contra a spec salva: **0 perdidas, 5 novas** (34 → 39) |
| `ready` da API Python (de dentro do container) | `200` |
| worker | `email_worker_identity` = `active` (`api-email-7`); nenhum `email_worker_disabled`, nenhum erro no log |
| fulfillment | continua `active` |
| público | `/` 200, `/api/health/ready` 200 |

### O que não foi validado

**Um e-mail real de compra.** Exige uma compra paga de verdade; a primeira que
acontecer gera a linha em `app_private.email_outbox` e o envio. O remetente em
si — SPF, DKIM, DMARC `pass` no cabeçalho recebido — foi validado na #355. Para
acompanhar: `select kind,status,attempt_count,error_code,message_id,sent_at
from app_private.email_outbox order by created_at desc limit 20`.

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/api:581a592 --stop-grace-period 10s \
  --env-rm SMTP_HOST_TRANSACIONAL --env-rm SMTP_PORT_TRANSACIONAL --env-rm SMTP_SECURE_TRANSACIONAL \
  --env-rm SMTP_USER_TRANSACIONAL --env-rm SMTP_PASS_TRANSACIONAL \
  --update-order stop-first sell-autonomia_api
```

~40 s. A migration `0049` é compatível com a API antiga e **fica**: descê-la
apaga a caixa de saída, e só se justifica com a fila vazia. Dump íntegro em
`$D/appsell_native_90_antes.dump`.

## Release `a139d86` — o celular para de baixar o que não desenha (2026-09-01)

Só o `web`, sem migration. Segundo release do dia no mesmo serviço.

As três telas do aparelho no hero são `display: none` abaixo de 680px e mesmo
assim eram baixadas: 149 KB dos 237 KB que o celular puxava de `/lp/`. Dentro de
`<picture>`, um `<img>` sem `src` não pede nada à rede quando nenhum `<source>`
casa a condição.

Dois seletores dependiam da ordem dos irmãos e impediam a mudança. Passaram a
apontar para `#t-painel` e `#t-recebe`, ids que a coreografia já usava.

### Validação em produção

| O quê | Resultado |
|---|---|
| peso de `/lp/` no iPhone 13 | **88 KB**, contra 237 KB antes |
| peso no desktop | inalterado, com as três telas, a peça e o vídeo |
| home, `/auth/signup`, `robots.txt`, `sitemap.xml` | `200` nos quatro |
| console do navegador | vazio em desktop e mobile |
| variáveis do serviço | 51 antes, 51 depois |

### Rollback

```bash
docker service update --image easypanel/sell-autonomia/web:9ebb93f \
  --update-order stop-first sell-autonomia_web
```

Spec anterior em `/tmp/sell-release-346/spec_web_antes.json` no host.

## Release `581a592` — o nome de quem entrou, e a primeira migration exercitada (2026-09-01)

**Primeiro release desta sequência que sai do "só o `web`".** Migration em banco
de produção, deploy da `api` e do `web`. `asset-worker` e `pdf-sandbox` ficaram
em `d5e00ec` de propósito: a mudança não os alcança.

### O que mudou para quem usa

O rodapé do menu mostra o nome da pessoa no lugar de "Sua conta", e a linha de
baixo passa a dizer a loja quando existe.

O Identity sempre soube o nome — o Cognito guarda `name`, `given_name` e
`family_name`, o client os declara legíveis, e o Appsell já pedia o escopo
`profile`. O claim chegava em todo login e era descartado ali mesmo: o sync
enviava só e-mail, e `identity_user` nunca teve coluna para guardá-lo.

A lacuna sempre existiu. Ficou visível quando o rodapé passou a mostrar dado
real, na #350 — antes ele dizia "Sua conta" para todo mundo, um texto fixo no
código. **Substituir um placeholder por dado real não cria a lacuna, revela a
que já estava lá.**

### O nome não passa pelo cofre de tokens

Foi a primeira ideia, e está descartada. A identidade selada valida suas chaves
por lista exata, e abrir essa fronteira com sessões ativas em produção
arriscaria derrubá-las para exibir um rótulo. Em vez disso o `callbackIdentity`
devolve o nome junto do `sessionId`, e o callback o entrega ao `PUT /v1/me` que
já acontece ali. Nenhuma chamada nova por carga de shell.

### A compatibilidade que abriu a ordem segura

A `0048` mantém `sync_identity` de **cinco** parâmetros viva ao lado da nova de
seis. Isso permitiu aplicar o schema **antes** de trocar a API, com o rollback
aberto o tempo todo — e foi verificado no meio do release: depois da migration
e antes do deploy, a API `d5e00ec` continuou saudável contra o schema novo.

A remoção da assinatura antiga fica para uma migration posterior, quando não
houver mais chamador.

### A primeira migration deste projeto exercitada por automação

Antes deste release, **nenhuma migration era exercitada**: `alembic` não
aparecia uma vez no workflow, e as suítes `test_postgres_*` não estão na lista
que o passo completo executa. As 47 anteriores chegaram a produção validadas
apenas por leitura.

O gate entrou junto (`scripts/ci/exercita_migrations.sh`): sobe a cadeia, desce
uma, sobe de novo, e confere que a cabeça aplicada é a declarada em
`reset_demo_tenant.py`. Roda só quando a rota `postgres` é selecionada.

O ciclo completo, e não só o `upgrade`, porque o defeito que interessa não é a
migration que não sobe — essa falha barulhenta na hora. É a que sobe e **não
desce**, descoberta durante um rollback.

O pré-requisito que faltava saber: o esquema exige os papéis `appsell_owner` e
`appsell_runtime` já existentes, com o migrador herdando o primeiro. Sem isso a
cadeia para em `permission denied for schema public`.

### Como foi aplicado

```bash
# 1. backups, antes de tocar em qualquer coisa
D=/tmp/sell-release-581a592-20260901-231248
mkdir -p $D
for s in api web; do docker service inspect sell-autonomia_$s > $D/spec_${s}_antes.json; done
docker exec <postgres-native-90> pg_dump -U $POSTGRES_USER -d appsell_native_90 -Fc > $D/appsell_native_90_antes.dump

# 2. fonte e imagens
git archive --format=tar origin/main | ssh <host> 'tar -x -C /home/claudete/appsell-src-581a592'
docker build -f deploy/easypanel-native/Dockerfile.api --build-arg GIT_SHA=581a592 -t easypanel/sell-autonomia/api:581a592 .
docker build -f deploy/easypanel-native/Dockerfile.web --build-arg GIT_SHA=581a592 -t easypanel/sell-autonomia/web:581a592 .

# 3. migration, com a API antiga ainda no ar
docker run --rm --network <rede> -v /home/claudete/appsell-src-581a592/apps/api:/mig -w /mig \
  easypanel/sell-autonomia/api:581a592 alembic upgrade head

# 4. serviços, nesta ordem
docker service update --image easypanel/sell-autonomia/api:581a592 --update-order stop-first sell-autonomia_api
docker service update --image easypanel/sell-autonomia/web:581a592 --update-order stop-first sell-autonomia_web
```

As credenciais do migrador nunca passaram pelo terminal: a DSN foi montada
dentro do container, a partir das variáveis do serviço de bootstrap.

### Validação executada em produção

| O quê | Resultado |
|---|---|
| head do banco | `0048_nome_de_quem_entrou`, com as **duas** assinaturas de `sync_identity` |
| coluna | `display_name` nullable, 14 identidades preservadas |
| API antiga contra schema novo | saudável — verificado entre a migration e o deploy |
| variáveis | api **33 → 33**, web **51 → 51**, nome a nome: nenhuma perdida |
| logs | zero `error`, `traceback`, `fatal` ou `panic` em api e web |
| home | `200` em 0,096 s |
| healthz do BFF | `redis`, `api` e `storage_upload` todos `true` |
| `/api/session` sem sessão | `401` |
| `/auth/signup` e `/settings/store/new` | `307` |
| worker e sandbox | intactos em `d5e00ec` |

### O que não foi validado

O nome só aparece **depois do primeiro login** de cada pessoa, porque é aí que
o claim é gravado. Logo após o deploy, **1 das 14 identidades** já tinha nome —
alguém entrou e o caminho funcionou de ponta a ponta. As outras 13 o ganham
conforme entrarem, e nenhuma ação é necessária para isso.

Se algum caminho de login não emitir o claim, o campo fica nulo e o rodapé
volta ao rótulo neutro, sem erro. Só se saberá que um caminho não emite quando
alguém entrar por ele e o nome não aparecer.

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/web:982d46e --update-order stop-first sell-autonomia_web
docker service update --image easypanel/sell-autonomia/api:d5e00ec --update-order stop-first sell-autonomia_api
```

A API `d5e00ec` volta a funcionar **sem** desfazer a migration, porque a
assinatura antiga continua viva. Se ainda assim for preciso descer o schema:
`alembic downgrade 0047_cota_de_armazenamento`, ciclo exercitado localmente e
no CI. Dump e specs em `/tmp/sell-release-581a592-20260901-231248`.

## Release `982d46e` — o painel parou de falar com quem não tem loja (2026-09-01)

Só o `web` mudou. Head do banco segue `0047_cota_de_armazenamento`.

Hotfix do release anterior, encontrado no **primeiro acesso real** depois dele.
Um comprador com curso ativo e nenhuma loja foi recebido pelo painel do
vendedor: título "Seu negócio em um só lugar", botão **Criar produto** que
daria erro por falta de loja e de permissão, e três métricas zeradas. O menu,
correto quanto aos itens, oferecia "Criar minha loja" como primeira ação a
quem tinha comprado um curso.

### A causa foi um critério de aceite declarado verde sem estar

O critério da #350 dizia que quem não tem loja **nem acesso comprado** abriria
na criação da loja. `/api/session` nunca soube nada sobre entitlement, então
todo mundo sem loja virava aspirante a vendedor. O item foi marcado como
cumprido na PR e no fechamento da issue. Não estava.

Fica o registro: nenhum teste automatizado pegaria isso, porque os testes
cobriam a audiência derivada de `sellers`, que era o que eu tinha implementado
— não o critério que eu tinha escrito. O defeito apareceu quando uma pessoa
entrou com uma conta real.

### O que mudou

A correção **não ampliou o `/api/session`**, que já faz duas chamadas por carga
de shell e continua sendo o risco em observação. Cada tela decide com o dado
que já tem: o callback já resolvia a membership, o painel já conhece as lojas
da sessão, e a biblioteca já sabe se está vazia.

- Sem loja, a casa passa a ser a biblioteca. Quem entra para vender continua
  chegando à criação pelo `next` do "Vender agora"; quem parou no meio encontra
  o convite na biblioteca vazia, que é onde cai.
- O painel só promete um negócio a quem tem loja, e só oferece "Criar produto"
  onde existe catálogo.
- Passou a existir uma regra única — `canOpenFirstStore` — consultada pelo
  menu, pela biblioteca e pelo painel. Antes as três perguntavam a mesma coisa
  de jeitos diferentes e discordavam.
- Estado de loja virou **tri-state**: tem, comprovadamente não tem, e não sei.
  Ler indisponibilidade como "não tem" mandava vendedor existente para a
  biblioteca.

### Defeitos encontrados nas próprias correções

O revisor de segunda opinião rodou três vezes neste hotfix. Cada rodada achou
defeito novo introduzido pela correção da rodada anterior:

| Severidade | O que era |
|---|---|
| MÉDIO | A consulta de grant acrescentada ao callback ficava dentro do bloco que faz logout: um timeout no `GET /v1/me` desfazia a sessão e derrubava o login inteiro |
| MÉDIO | Menu, biblioteca e painel discordavam sobre quem pode abrir loja; superadmin sem lojas recebia o formulário de criação no painel |
| MÉDIO | A persona demo de admin perdeu o botão "Criar produto", porque `identity` é null no demo |
| BAIXO | Payload irreconhecível contava como "zero lojas comprovadas" e reclassificava vendedor durante rollout |
| BAIXO | Loading e falha eram tratados como ausência de loja: vendedor com sessão lenta via "Comece criando sua loja" |
| BAIXO | `platform_support` e `platform_auditor` recebiam o convite de criar loja |
| BAIXO | Os testes liam o texto do arquivo e passariam com a condição invertida |

### Como foi aplicado

```bash
mkdir -p /tmp/sell-release-982d46e
docker service inspect sell-autonomia_web > /tmp/sell-release-982d46e/spec_web_antes.json

git archive --format=tar origin/main | ssh <host> \
  'mkdir -p /home/claudete/appsell-src-982d46e && tar -x -C /home/claudete/appsell-src-982d46e'

cd /home/claudete/appsell-src-982d46e
docker build -f deploy/easypanel-native/Dockerfile.web \
  --build-arg GIT_SHA=982d46e -t easypanel/sell-autonomia/web:982d46e .

docker service update --image easypanel/sell-autonomia/web:982d46e \
  --update-order stop-first sell-autonomia_web
```

### Validação executada em produção

| O quê | Resultado |
|---|---|
| home | `200` |
| healthz do BFF | `redis`, `api` e `storage_upload` todos `true` |
| `/auth/signup` | `307` |
| `/settings/store/new` sem sessão | `307` |
| logs do `web` | zero `error`, `fatal` ou `panic` em 3 minutos |
| variáveis de ambiente | 51 antes, 51 depois, nome a nome: nenhuma perdida, nenhuma nova |
| demais serviços | api, worker e sandbox intactos em `d5e00ec` |
| suíte | 1048 passando no `test:fast` |

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/web:3bcae6e \
  --update-order stop-first sell-autonomia_web
```

Spec anterior em `/tmp/sell-release-982d46e/spec_web_antes.json`.

## Release `3bcae6e` — a navegação passa a saber quem está logado (2026-09-01)

Só o `web` mudou. Nenhuma alteração de API, worker, sandbox ou schema: a head
do banco continua `0047_cota_de_armazenamento` e não há migration para reverter.

Entrega a #350 e fecha a #305. A navegação do shell era uma lista fixa e
`/api/session` não devolvia papel algum, então em produção real todo mundo via
Visão geral, Produtos e Recebimentos — inclusive quem só tinha comprado um
curso. Agora a sessão resolve o papel no servidor, a partir de `GET /v1/me` e
`GET /v1/me/sellers`, e a navegação deriva disso.

### O que observar nos próximos dias

Esta é a primeira versão em que `/api/session` faz **duas chamadas à API
interna a cada carga do shell**. A API roda com pool de cinco conexões, sem
overflow, e esse efeito nunca foi medido sob carga real — é o risco que sobrou
depois do review. Se aparecer lentidão no shell ou saturação de conexão na API,
esse é o primeiro lugar a olhar.

A resolução falha **fechada**: quando a API de domínio não responde, o shell
mostra recuperação em vez de adivinhar o papel. Isso é deliberado, mas troca
"menu errado" por "tela de acesso indisponível" durante uma indisponibilidade
da API. O comportamento é novo em produção.

### Como foi aplicado

```bash
# 1. backup antes de tocar em qualquer coisa
mkdir -p /tmp/sell-release-350
docker service inspect sell-autonomia_web > /tmp/sell-release-350/spec_web_antes.json
docker service inspect sell-autonomia_web \
  --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' > /tmp/sell-release-350/imagem_anterior.txt

# 2. fonte no host
git archive --format=tar origin/main | ssh <host> \
  'mkdir -p /home/claudete/appsell-src-3bcae6e && tar -x -C /home/claudete/appsell-src-3bcae6e'

# 3. build
cd /home/claudete/appsell-src-3bcae6e
docker build -f deploy/easypanel-native/Dockerfile.web \
  --build-arg GIT_SHA=3bcae6e -t easypanel/sell-autonomia/web:3bcae6e .

# 4. troca
docker service update --image easypanel/sell-autonomia/web:3bcae6e \
  --update-order stop-first sell-autonomia_web
```

### Validação executada em produção

| O quê | Resultado |
|---|---|
| home | `200` em 0,36 s |
| healthz do BFF | `redis`, `api` e `storage_upload` todos `true` |
| `/auth/signup` | `307` para `auth.autonomia.site/register` |
| `/auth/login` | `307` para `auth.autonomia.site/login/`, destino diferente |
| `/api/session` sem sessão | `401` |
| `/settings/store/new` sem sessão | `307` para a home — a rota nova entrou no matcher do proxy |
| "Vender agora" na página pública | leva `next=%2Fsettings%2Fstore%2Fnew` nas quatro ocorrências |
| logs do `web` | zero `error`, `traceback`, `fatal` ou `panic` em 5 minutos |
| variáveis de ambiente | 51 nomes antes, 51 depois, comparados **nome a nome**: nenhuma perdida, nenhuma nova |
| demais serviços | api, worker e sandbox intactos em `d5e00ec` |

### O que não foi validado

A suíte de identidade real não rodou: exige chaves que não estão no
`credential.env` e os dois repositórios de identity montados. O
`tests/bff-api-onboarding.test.ts`, que cobre login sem loja → criar loja →
primeiro produto, **não roda em lugar nenhum** — depende de
`APPSELL_TEST_DOMAIN_API_URL`, ausente localmente e no workflow. É a #266.

O Playwright local dá 20 falhas por ambiente, **idênticas no commit anterior ao
merge**, o que prova ausência de regressão mas não prova funcionamento.

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/web:a139d86 \
  --update-order stop-first sell-autonomia_web
```

A imagem `a139d86` está no host, ao lado de `9ebb93f`, `d5e00ec` e `8930313`.
Spec anterior em `/tmp/sell-release-350/spec_web_antes.json`.

## Release `9ebb93f` — página pública nova (2026-09-01)

Só o `web` mudou. Nenhuma alteração de API, worker, sandbox ou schema, então o
rollback é um comando e não há migration para reverter.

### Como foi aplicado

```bash
# 1. backup da spec, antes de tocar em qualquer coisa
mkdir -p /tmp/sell-release-lp
docker service inspect sell-autonomia_web > /tmp/sell-release-lp/spec_web_antes.json
docker service inspect sell-autonomia_web \
  --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' > /tmp/sell-release-lp/imagem_anterior.txt

# 2. fonte no host, no padrao de sempre
git archive --format=tar origin/main | ssh <host> \
  'mkdir -p /home/claudete/appsell-src-9ebb93f && tar -x -C /home/claudete/appsell-src-9ebb93f'

# 3. build
cd /home/claudete/appsell-src-9ebb93f
docker build -f deploy/easypanel-native/Dockerfile.web \
  --build-arg GIT_SHA=9ebb93f -t easypanel/sell-autonomia/web:9ebb93f .

# 4. imagem e SMTP na mesma atualizacao, para um unico stop-first
docker service update --image easypanel/sell-autonomia/web:9ebb93f \
  --env-add SMTP_HOST_COMERCIAL=... --env-add SMTP_PORT_COMERCIAL=... \
  --env-add SMTP_SECURE_COMERCIAL=... --env-add SMTP_USER_COMERCIAL=sell.comercial@autonomia.site \
  --env-add SMTP_PASS_COMERCIAL=... \
  --update-order stop-first sell-autonomia_web
```

Os valores do SMTP vêm do `.env.local` do google-saas e nunca passaram por
terminal, arquivo intermediário ou linha de comando no host: foram lidos para
variáveis de shell e entregues pelo stdin do ssh.

### A contagem de variáveis que parecia perda e não era

A trava manda abortar se o número de variáveis cair. Ele foi de **47 para 51**,
e cinco tinham sido adicionadas: 47 + 5 daria 52.

A comparação nome a nome contra a spec salva mostrou **nenhuma perdida**. A
causa é que `PORT` estava **duplicada** na spec anterior: 47 entradas para 46
nomes distintos. O `docker service update` deduplicou.

Fica a lição para a próxima leitura desse contador: **comparar nomes, não
contar entradas.** O número sozinho teria provocado um rollback desnecessário.

### Validação executada em produção

| O quê | Resultado |
|---|---|
| home | `200` em 0,10 s, com título, canonical e og:image corretos |
| `/auth/signup` | `307` para `auth.autonomia.site/register`, a tela de cadastro |
| `/auth/login` | `307` para `auth.autonomia.site/login/`, destino diferente |
| `robots.txt` e `sitemap.xml` | `200` nos dois; antes davam `404` |
| `POST /api/contato` | `200 {"ok":true}` em 2,0 s, e o e-mail chegou |
| console do navegador | vazio em desktop e iPhone |
| logs do `web` | sem `error`, `traceback`, `fatal` ou `panic` |
| demais serviços | api, worker e sandbox intactos em `d5e00ec` |

### Rollback deste release

```bash
docker service update --image easypanel/sell-autonomia/web:d5e00ec \
  --update-order stop-first sell-autonomia_web
```

A imagem `web:8930313` também continua no host, como segundo degrau. As
variáveis de SMTP podem ficar: nenhum código anterior as lê.

### O que ficou aberto

- 149 KB de imagem do palco baixados no celular sem serem desenhados
- a página não tem navegação no celular: para chegar a Preço ou FAQ, só rolando
  ou pelos links do rodapé
- as lojas públicas estão fora do `sitemap.xml` até existir listagem no backend
- o sitemap ainda não foi submetido ao Search Console

## Release `d5e00ec` — o vídeo longo (2026-09-01)

O vendedor não conseguia publicar aula de duração real: teto de 100 MiB, envio
num `PUT` único que morria em cinco minutos, e o vídeo chegando ao aluno com o
índice do MP4 no fim do arquivo, o que deixa a barra de progresso inútil.

Depois deste release ele publica aula de 30 a 45 minutos, o arquivo sobe em
partes e retoma sozinho, e o aluno arrasta a barra.

### Como foi aplicado

Ordem obrigatória, e o runbook explica por quê:
**migrations → pdf-sandbox → asset-worker → web → api**. Duas combinações de
versões quebram durante a janela, e essa sequência é a única que não as produz.

O passo do `pdf-sandbox` **não constrói imagem**: ela é a imagem da `api` com
outra tag, e quem faz o serviço virar sandbox é o comando que a spec sobrescreve.

```bash
docker tag easypanel/sell-autonomia/api:d5e00ec easypanel/sell-autonomia/pdf-sandbox:d5e00ec
```

O runbook completo está em
[runbooks/video-longo-325.md](runbooks/video-longo-325.md).

### Validação executada em produção

- banco em `0047_cota_de_armazenamento`, `schema_ready() = true`
- teto de vídeo `536870912`, teto de PDF `104857600` **inalterado**
- cota do vendedor `21474836480` (20 GiB), cota em voo `4294967296` (4 GiB)
- `public` **não** executa `seller_storage_quota_bytes()`
- os quatro serviços em `1/1`, com a contagem de variáveis idêntica ao backup
  (api 33, sandbox 1, worker 32, web 47)
- `/health/ready` da api → `{"status":"ok","database":true,"storage":true,"pdf_sandbox":true}`
- `https://sell.autonomia.site/api/health/ready` → `{"status":"ok","redis":true,"api":true,"storage_upload":true}`
- socket do sandbox recriado, e ele saudável **depois** de um trabalho de mídia
- zero erros no log dos quatro serviços nos dez minutos seguintes

**A prova de verdade**, feita pelo socket real de produção: um MP4 gerado com o
índice no fim, mandado ao `/prepare-video` em modo catálogo, voltou `200` com
`x-appsell-recoded: false` e **com o índice no começo**. É exatamente o que o
release existe para fazer.

### O que ficou pendente

A regra que expira partes órfãs **não foi aplicada**, e o aviso apareceu no log
como projetado ([#339](https://github.com/autonom-ia/appsell/issues/339)). São
dois problemas: a credencial do worker não escreve política de bucket, e o
formato da regra é recusado por este MinIO mesmo com credencial root. Não
bloqueia: cancelar um envio continua abortando as partes de verdade, que é a
defesa principal.

### Rollback deste release

```bash
docker service update --image .../api:12d8763 --update-order stop-first sell-autonomia_api
docker service update --image .../api:12d8763 --update-order stop-first sell-autonomia_asset-worker
docker service update --image .../web:8930313 --update-order stop-first sell-autonomia_web
docker service update --image .../pdf-sandbox:712e5bb --limit-cpu 4.0 --limit-memory 4G \
  --no-healthcheck --update-order stop-first sell-autonomia_pdf-sandbox
```

As migrations **não precisam voltar**: são compatíveis com as imagens antigas. A
única parte que aperta é o teto de bytes em voo, e ele só morde um vendedor com
mais de 4 GiB parados em quarentena.

**Atenção:** depois que o primeiro vídeo acima de 100 MiB for publicado, voltar
as imagens antigas deixa essa aula inacessível mesmo com health verde. A partir
daí o caminho é corrigir para frente.

## Release `712e5bb` — PDF de 300 páginas (2026-08-31)

O teto era 100, e o playbook do vendedor tem 83 — o próximo documento um pouco
maior não passaria. O número subiu em **nove camadas de uma vez**, e o pipeline
foi ajustado para aguentar o documento que o número promete.

### Como foi aplicado

```bash
# 1. backup das specs
mkdir -p /tmp/backup-specs-<data>
for s in api pdf-sandbox asset-worker web; do
  docker service inspect sell-autonomia_$s > /tmp/backup-specs-<data>/$s.json
done

# 2. migration — a imagem tem só `src/`, então o alembic roda do fonte extraído
docker run --rm --network container:<api> -v /tmp/b712/apps/api:/work -w /work \
  -e APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_native_admin:<senha>@sell-autonomia_postgres-native-90:5432/appsell_native_90" \
  --entrypoint sh easypanel/sell-autonomia/api:712e5bb -c 'cd /work && python -m alembic upgrade head'

# 3. serviços, nesta ordem, sempre stop-first
docker service update --image .../api:712e5bb --update-order stop-first sell-autonomia_asset-worker
docker service update --image .../pdf-sandbox:712e5bb --limit-cpu 4.0 --limit-memory 4G \
  --no-healthcheck --update-order stop-first sell-autonomia_pdf-sandbox
docker service update --image .../api:712e5bb --update-order stop-first sell-autonomia_api
```

Três detalhes que custam tempo se esquecidos: a variável do alembic é
`APPSELL_MIGRATOR_DATABASE_URL` (não a da API), a senha de admin vem do env do
serviço `bootstrap-native-90`, e o `pdf-sandbox` **sempre** com
`--no-healthcheck` (ele escuta num socket unix) e **nunca** com `start-first`.

### Validação executada em produção

- `alembic_version = 0044_pdf_de_trezentas_paginas` e `schema_ready() = true`
- os onze pontos do banco em 300 páginas / 384 MiB — seis restrições, duas de
  tamanho, três funções
- dentro do processo: `paginas: 300 | pacote: 384 MiB | prazo: 420 s`
- no sandbox: `cota de 4 núcleos -> 4 rasterizadores`
- o playbook real, pelo extrator de produção: **32 links, 20 de índice e 12 de
  vídeo**, com os dois vídeos da página 27

### Rollback deste release

```bash
docker service update --image .../api:ee194bf sell-autonomia_api
docker service update --image .../pdf-sandbox:ee194bf \
  --limit-cpu 1.0 --limit-memory 1G --no-healthcheck sell-autonomia_pdf-sandbox
```

A migration **não precisa voltar**: é uma expansão compatível com as imagens
antigas. E depois que existir um PDF acima de 100 páginas publicado, voltar as
imagens antigas deixa esse documento inacessível *mesmo com health verde* — o
caminho certo aí é corrigir para frente, não descer. A guarda do `downgrade`
recusa descer se houver dado que não caiba, cobrindo oito estados.

## Release `ee194bf` — o socket do sandbox, e o alarme que mentia (2026-08-30)

Nasceu de um incidente que eu mesma causei: um deploy do `pdf-sandbox` com
`--update-order start-first` apagou o socket unix do serviço, e **todo PDF passou
a abrir sem botão nenhum** — sem erro na tela e sem nada nos logs.

A causa: o `finally` do runner removia o socket **sem verificar se era o dele**.
Com dois containers convivendo, o antigo encerrou depois de o novo ter publicado
o seu, e apagou o do sucessor.

Três decisões consertaram, e nenhuma depende de os processos combinarem algo:
nome sorteado (os dois containers têm pid 1), `listen` antes de publicar, e
**ninguém remove socket de ninguém** — o `rename` do próximo substitui, de forma
atômica.

**E o alarme mentia por construção.** `pdf_sandbox_required` valia
`storage_configured and environment in {local, ci}`, ou seja, `False` em
produção — e a sonda retornava `True` na primeira linha sem sondar nada. O
`healthz` respondeu `pdf_sandbox: true` durante todo o incidente, e foi por isso
que eu reverti a imagem, vi tudo verde e anunciei que estava resolvido com o
produto quebrado.

Agora o critério é ter sido apontado para o serviço, e o `ready` tem
consequência: o `HEALTHCHECK` do contêiner consome esse endpoint, com
`interval=10s` e `retries=6`. Sandbox fora por mais de um minuto derruba a task
— deliberado, porque foi a promoção silenciosa de uma task quebrada que causou o
incidente.

### O que aprendi, e está registrado

- **Nunca `--update-order start-first` no `pdf-sandbox`.**
- **Rollback não é prova; verificação é.** O problema nunca esteve na imagem, e
  eu anunciei "revertido" sem abrir um PDF.
- **A verificação depois de qualquer deploy é abrir um PDF com botão de vídeo**
  — não `healthz`, e não "0 linhas de erro", porque a API não escreve log
  ([#312](https://github.com/autonom-ia/appsell/issues/312)).

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

- **PDF acima de 100 páginas era recusado** — corrigido na #324, **ainda não em
  produção**. O teto passa para 300 nas nove camadas de uma vez: Python, TypeScript,
  seis restrições do banco, três funções PL/pgSQL, o pacote rasterizado (128 →
  384 MiB) e a lista de versões que o banco reconhece.

  Uma tentativa anterior alargava parte delas e o review pegou que estava pela
  metade — alargar parte só muda a porta em que o vendedor bate. Fecha a
  [#293](https://github.com/autonom-ia/appsell/issues/293).

  O 300 vem de medição em produção, não de estimativa: 0,73 s por página a 150
  dpi, com 83 MiB de pico **constantes** — o `pdftoppm` desenha uma página de
  cada vez, então memória não é o que limita. Trezentas páginas são 3,7 minutos
  de rasterização, uma vez, no envio. O que impede subir mais é o serviço
  isolado atender um arquivo por vez: enquanto rasteriza, ninguém consegue ler
  os links de PDF nenhum ([#320](https://github.com/autonom-ia/appsell/issues/320)).
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
