# Estado atual — Autonom.ia Sell

> Fonte operacional para retomar o projeto sem depender do histórico da conversa.
>
> Última verificação documentada: **2026-09-01**. Esta página separa o que foi
> comprovado na produção do que continua sendo plano ou laboratório Local/CI.

## Release em produção

> Última verificação: **2026-09-01**, lendo o host — não a memória desta página.
> Esta página já divergiu da realidade três vezes; o que vale é o que
> `docker service ls` responde.

| Item | Estado |
|---|---|
| Domínio | `https://sell.autonomia.site` |
| Ambiente | VPS EasyPanel nativo, Docker Swarm |
| SHA do **api** | `d5e00ec` |
| SHA do **asset-worker** | `d5e00ec` — mesma imagem da api |
| SHA do **pdf-sandbox** | `d5e00ec` — mesma imagem da api, com outra tag |
| SHA do **web** | `d5e00ec` |
| Banco | head **`0047_cota_de_armazenamento`**, `schema_ready() = true` |

**Pela primeira vez os quatro serviços andam no mesmo SHA.** O release do vídeo
longo obrigou o `pdf-sandbox` a subir junto, porque o modo que recusa
recodificar mora nele.

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
