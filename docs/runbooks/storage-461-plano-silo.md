# Troca do MinIO pelo Silo no storage do Sell (#461)

Plano da trilha Plataforma, 24/10/2026. Rodrigo autorizou: "Pode trocar o
armazenamento e fazer os testes". **Este documento não muda nada em produção.**
Cada passo 🔴 abaixo pede o OK dele, um de cada vez.

As opções e o ensaio local que levaram ao Silo estão no
`storage-461-opcoes.md` (#474). Aqui ficam só a execução, a volta e o que foi
lido no host para escrevê-la.

## O que foi lido no host hoje (só leitura)

| Fato | Valor lido |
|---|---|
| Serviços do Sell com MinIO | `sell-autonomia_storage` e `sell-autonomia_view-storage`, os dois em `minio/minio@sha256:14cea493…936e` |
| Versão | `RELEASE.2025-09-07T16-13-09Z` (commit `07c3a429`) nos dois. É **a mesma** do ensaio local do #474 |
| Modo | `xl-single` nos dois (`format.json`): um disco, um servidor |
| Comando | storage: `minio server /data --console-address :9001`; view-storage: `minio server /data` |
| Volume | `sell-autonomia_storage_data` 197 MiB; `sell-autonomia_view-storage_data` 2,1 GiB |
| Buckets do `storage` | `appsell-ready` 196 MiB, `appsell-backups` 152 KiB, `appsell-quarantine` 16 KiB |
| Buckets do `view-storage` | `autonomia-geral` |
| Ordem de update | `stop-first` / rollback `stop-first` nos dois |
| Disco livre | 215 GiB no `/` (o `/var/lib/docker` está no mesmo disco) |
| Imagem de volta em cache | `minio/minio:latest`, ID `69b2ec208575`, mesmo digest dos serviços. **Não dá para baixar de novo**: o `minio/minio` dá 404 no Docker Hub |

### Quem fala com o storage

Lido nos nomes das variáveis dos serviços e nas rotas do Traefik. Nenhum valor
foi impresso.

| Cliente | Como fala | Muda na troca? |
|---|---|---|
| `api` | `APPSELL_API_STORAGE_*`: endpoint público e interno, chaves, buckets | não: o nome do serviço, as chaves e os buckets ficam |
| `asset-worker` | as mesmas `APPSELL_API_STORAGE_*` da api | não |
| `web` | `APPSELL_STORAGE_PUBLIC_ORIGIN` | não |
| navegador | URL assinada em `https://minio.autonomia.site`, roteador `appsell-storage-https` (prioridade 100) → `sell-autonomia_storage:9000` | não: a rota aponta para o serviço, não para a imagem |
| backup do banco, destino `s3` | `minio/mc` (`lib-backup.sh`) | **sim**, ver "Backup" |
| `view-storage` | **nenhum serviço** tem variável que aponte para ele, e nenhuma rota do Traefik o publica | dono a confirmar antes |

### Achados que este plano não resolve

São fatos lidos hoje, que ficam para Issue própria e para decisão do Rodrigo:

1. **O último backup do banco é de 22/08/2026** (`/var/backups/appsell-native-90`).
   O host não tem cron nem timer de backup do Sell. O destino `s3` também não
   roda: a imagem `minio/mc` não está no host e não existe mais no Docker Hub.
2. **O cron `docker-image-prune`** roda às 00:39 com
   `docker image prune -af --filter until=24h`. Ele apaga toda imagem sem
   contêiner que tenha sido *criada* há mais de 24 h, com tag ou sem. Uma
   imagem de volta guardada só como tag some na primeira madrugada. Isso vale
   para este plano e para a tag de volta do web (#437).
3. **`minio.autonomia.site` em `http`** é roteado pelo Easypanel para o
   `dados_minio`, que é de outro projeto (`main.yaml`, `http-dados_minio-0`). Em
   `https` ele vai para o storage do Sell. O `dados_minio` usa a mesma imagem
   `minio/minio:latest` e fica fora deste plano. Não mexer sem o dono dele.

## Decisões

- **Troca no lugar, sem cópia de dado.** O Silo lê o formato do MinIO, e o
  ensaio local do #474 conferiu VersionId, ETag, multipart fechado e multipart
  em andamento, nos dois sentidos. O `version_id` que o banco guarda continua
  valendo.
- **`stop-first`, como a spec já está.** Dois servidores não podem abrir o mesmo
  disco `xl-single`. A loja fica sem storage pelo tempo de subir o Silo; o
  ensaio mede quanto é.
- **Um serviço por vez:** primeiro o `storage` do Sell, depois o `view-storage`,
  e só quando o dono dele for conhecido.
- **Mesmas variáveis `MINIO_*`, mesmo volume, mesmas redes e aliases.** Na spec,
  mudam só a imagem e o comando.

## Etapa 0 — CI contra o Silo (PR próprio)

O `test-storage-minio.sh` ganha um segundo passe, contra o binário linux-amd64
do Silo conferido por SHA-256 (no #474). Ele roda o mesmo contrato do passe do
MinIO. Pronto quando o `cert-storage` passar nos dois.

## Etapa 1 — ensaio isolado com cópia da produção 🔴

Cópia dos dois volumes em volumes novos, num namespace de ensaio no mesmo host.
Mesmas garantias do `deploy/preview-vps/README.md`: sem Swarm, sem Traefik, sem
porta publicada, rede `internal`, limites de CPU e memória, e remoção no fim.
Os dados não saem do host.

1. Copiar cada volume, só lendo a origem:
   `docker run --rm -v <volume>:/src:ro -v ensaio-<volume>:/dst <alpine por digest> cp -a /src/. /dst/`.
   O MinIO de produção continua rodando. Por isso o ensaio pode pegar um
   envio pela metade, e isso não é falha do ensaio.
2. Subir o **MinIO** de produção (o ID `69b2ec208575`) na cópia e gerar o
   inventário: para cada bucket, `ListObjectVersions` completo (chave,
   `VersionId`, ETag, tamanho, `IsLatest`), `ListMultipartUploads`,
   versionamento e lifecycle de cada bucket. Gravar o SHA-256 do inventário.
3. Parar o MinIO. Subir o **Silo** (`pgsty/silo@sha256:2636fc41…69d7`) na mesma
   cópia e gerar o inventário de novo. **Tem de bater byte a byte** com o do
   passo 2.
4. Ler pelo Silo: `GET` por `VersionId` de uma amostra de cada bucket,
   conferindo o SHA-256 do conteúdo contra o do MinIO.
5. Conferir, sem supor, três coisas que o README do Silo afirma:
   - o binário e o comando (`silo server /data`), e se a imagem tem `/bin/sh`;
   - `/minio/health/live` e `/minio/health/ready` respondem;
   - as variáveis `MINIO_*` são lidas.
   Medir quanto tempo o Silo leva para responder ao health. Esse é o tempo da
   loja sem storage na etapa 3.
6. Gravar pelo Silo um objeto versionado. Parar o Silo, subir o MinIO de novo
   na cópia, e conferir o inventário do passo 3 mais o objeto novo. Isso
   prova a volta com dado escrito pelo Silo.
7. Remover os contêineres e volumes do ensaio. Registrar as saídas, só
   contagens e hashes, no #461.

**Abortar o plano** se o inventário divergir, se algum `GET` por versão falhar
ou se a volta do passo 6 não ler tudo.

## Etapa 2 — preparação, no dia 🔴

1. **Guardar a imagem de volta em arquivo**, porque a tag não sobrevive ao
   prune (achado 2):
   `docker save 69b2ec208575 -o <caminho do runbook>/minio-RELEASE.2025-09-07.tar`,
   mais o SHA-256 do arquivo. Conferir com
   `docker load -i` num host de teste, ou ao menos com `tar -tf`.
2. **Backup do banco** novo, `local-dir`, com o `backup-native.sh` (achado 1).
   O banco não muda na troca, mas o último backup tem dois meses.
3. **Snapshot da VPS** na Hostinger.
4. `docker service inspect` do `storage` salvo em
   `/tmp/sell-autonomia_storage_spec_<timestamp>.json`, mais o inventário do
   passo 2 da etapa 1, agora contra a produção (só leitura, pela api de
   controle).
5. Pré-condições, cada uma com um abort:
   - task do storage `Running`;
   - `api` com `/health/ready` em 200;
   - o asset-worker sem job em curso no log;
   - memória e disco como no `release-437-correcoes-rce.md`.

## Etapa 3 — troca do `storage` 🔴

```sh
sudo -n docker service update \
  --image pgsty/silo@sha256:<digest amd64 conferido na etapa 1> \
  --args "-c 'silo server /data --console-address :9001'" \
  sell-autonomia_storage
```

- Sem `--update-order`: a spec já é `stop-first`, e é o que esse serviço exige.
- Sem `--env-*`, `--mount-*` ou `--network-*`. Depois da troca, o digital da
  spec (como no `storage-rede-interna.sh`) tem de mudar **só** em `Image` e
  `Args`.
- A spec roda `/bin/sh` com `Args` `["-c", "…"]`. O `--args` troca só os
  argumentos, e o `/bin/sh` continua. Por isso o passo 5 da etapa 1 confere
  também se a imagem do Silo tem `/bin/sh`.
- O comando final sai do passo 5 da etapa 1. Se lá o binário ou a forma de
  `--args` forem outros, este bloco é corrigido antes do OK.

Conferências, nesta ordem:

1. A task nova está `Running` na imagem do Silo, e
   `UpdateStatus.State == completed`.
2. `/minio/health/live` e `/minio/health/ready` respondem pela rede interna.
3. O inventário da produção é igual ao do passo 4 da etapa 2.
4. A `api` responde `/health/ready` em 200 três vezes.
5. O `storage_public_audit` de fora.
6. Na loja: envio de capa, de PDF e de vídeo em partes, o asset-worker leva cada
   um da quarentena ao `appsell-ready`, e um comprador lê o PDF e o vídeo.

## Volta 🔴 (pré-aprovada junto com a troca, sem OK novo)

Se qualquer conferência falhar:

```sh
sudo -n docker service update \
  --image minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e \
  --args "-c 'minio server /data --console-address :9001'" \
  sell-autonomia_storage
```

- Se a imagem não estiver mais no host: `docker load -i` do arquivo do passo 1
  da etapa 2, conferindo o SHA-256, e o mesmo comando.
- O mesmo volume, sem cópia. A volta com dado escrito pelo Silo foi provada no
  passo 6 da etapa 1.
- Depois, as mesmas conferências 1 a 5, e o registro no #461.
- **Último recurso**, se o MinIO não ler o volume: restaurar o snapshot da
  Hostinger. Isso volta a VPS inteira, com Manu, Lili e n8n juntos, e por isso
  exige decisão do Rodrigo na hora.

## Etapa 4 — `view-storage` 🔴

Só depois de saber quem usa o bucket `autonomia-geral`: hoje nenhum serviço e
nenhuma rota apontam para ele. O mesmo roteiro das etapas 2 e 3, com
`--args "-c 'silo server /data'"`. Se não houver dono, a decisão é do Rodrigo:
trocar junto, ou desligar com backup.

## Backup: o destino `s3` sem `minio/mc` (PR próprio)

Proposta do #474, mantida: o `BACKUP_MC_IMAGE` passa a ser a imagem do Silo
por digest, com `--entrypoint mcli`. O PR precisa provar que o `mcli` lê
`MC_HOST_offhost`, e ensaiar o destino `s3` contra o storage do CI. Isso não
resolve o achado 1: o backup não está agendado, e o bucket `appsell-backups`
fica no mesmo volume do storage, ou seja, não é off-host.

## Relação com o resto do #461

- A troca mantém o nome do serviço e o alias `sell-autonomia-storage`. Por
  isso as etapas 4 a 6b do `storage-461-rede-interna.md` valem antes ou
  depois desta, sem ordem fixa.
- A borda do #477 filtra rotas, não a imagem. O passo 5 da etapa 1 confere
  que `/minio/health/*`, que ela deixa passar, continua respondendo.
- Uma troca de cada vez: não executar esta no mesmo dia de outra etapa do
  #461.
