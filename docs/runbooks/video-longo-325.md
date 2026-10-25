# Runbook — deploy do vídeo longo (#325): migrations 0045 a 0047 e os quatro serviços

> **Status:** plano preparado em 2026-09-01, **ainda não executado**. O release
> em produção continua sendo o registrado em
> [PRODUCTION_CURRENT.md](../PRODUCTION_CURRENT.md). Não executar sem aprovação
> explícita do Rodrigo, backup e o rollback abaixo à mão.
>
> Escopo: subir em `sell.autonomia.site` (EasyPanel nativo, Docker Swarm, VPS
> compartilhada — cross-project safety vale) o SHA da `main` no momento da
> execução. Os comandos abaixo escrevem `<sha>`: substituir uma vez, no começo,
> e conferir que é o mesmo em todos. SHA embutido em oito comandos envelhece
> mal, e este repositório já foi mordido por isso.
> Pré-requisitos: PRs [#332](https://github.com/autonom-ia/appsell/pull/332),
> [#334](https://github.com/autonom-ia/appsell/pull/334) e a da cota
> ([#335](https://github.com/autonom-ia/appsell/issues/335)) mergeadas em `main`
> com CI verde e três revisões independentes aplicadas.

## O que este release entrega

O vídeo do catálogo passa a ser guardado com o índice do MP4 no começo do
arquivo, então a barra de progresso do aluno funciona. O envio passa a ser em
partes, com retomada, então uma queda de rede custa uma parte e não o arquivo
inteiro. E o teto sobe de 100 MiB para 512 MiB, o que faz a aula de 30 a 45
minutos caber.

Em `main`, sem deploy: `07176d3` (faststart e tetos), `d47599d` (envio em
partes e teto novo) e a da cota do vendedor
([#335](https://github.com/autonom-ia/appsell/issues/335)).

> **Porta de mão única.** Depois que o primeiro vídeo acima de 100 MiB for
> publicado, voltar as imagens antigas deixa essa aula **inacessível, mesmo com
> health verde** — a mesma lição do PDF de 300 páginas. A partir daí o caminho é
> corrigir para frente, não descer. O rollback deste documento só é limpo
> enquanto nenhum vídeo grande tiver sido publicado.

## Quem precisa se mover, e por quê

Diferente do release `12d8763`, desta vez **os quatro serviços mudam**. O
`pdf-sandbox` não pode ficar para trás: o modo que recusa recodificar mora nele.

| Serviço | Imagem | Por que precisa subir |
|---|---|---|
| `pdf-sandbox` | pdf-sandbox | O modo catálogo do `/prepare-video`: recusa recodificar e confere a saída. |
| `asset-worker` | api | Manda o vídeo ao sandbox e guarda o derivado, não os bytes crus. |
| `web` | web | A tela que envia em partes e retoma, mais as rotas novas do BFF. |
| `api` | api | O plano de partes na intenção, e os endpoints de assinar e listar parte. |

## A ordem, e por que é essa

A ordem não é preferência. Cada par de versões que conviveria durante a janela
foi conferido, e só uma sequência não tem janela quebrada.

| Combinação durante a janela | Resultado |
|---|---|
| worker novo + sandbox velho | **Quebra.** O sandbox velho ignora o cabeçalho do modo, aceita recodificar, e o `ffmpeg` de uma hora segura o único lugar dele enquanto o cliente desiste em cinco minutos. |
| api nova + web velho | **Quebra.** A api oferece plano de partes acima de 16 MiB, e a tela velha não sabe lê-lo: o envio falha. |
| sandbox novo + worker velho | Compatível. O `/probe-video` continua existindo, e sem o cabeçalho o comportamento é o de antes. |
| web novo + api velha | Compatível. A api velha nunca devolve plano de partes, e a tela nova cai no envio direto. |
| migrations novas + tudo velho | Compatível. O código velho nunca manda hash nulo e continua limitado a 100 MiB do lado dele. **Uma ressalva:** o teto de bytes em voo da 0047 aperta em vez de alargar, e recusaria um vendedor com mais de 4 GiB parados em quarentena. Com o teto antigo de 100 MiB isso exigiria mais de quarenta envios abandonados do mesmo vendedor. |

Daí a sequência: **migrations → pdf-sandbox → asset-worker → web → api**.

## Antes de tocar em qualquer coisa

> **Armadilha já registrada.** `web` e `api` já rodaram imagem **sem tag**
> (`:latest`). Reconstruir sobrescreve a tag e **não sobra rollback**. Marcar a
> imagem em uso, com o SHA que ela carrega, antes de qualquer build.

```bash
# 1. o que está no ar agora, lido do host e não deste documento
for s in api pdf-sandbox asset-worker web; do
  docker service inspect sell-autonomia_$s \
    --format '{{.Spec.Name}} {{.Spec.TaskTemplate.ContainerSpec.Image}}'
done

# 2. specs, para poder restaurar
mkdir -p /tmp/backup-specs-20260901
for s in api pdf-sandbox asset-worker web; do
  docker service inspect sell-autonomia_$s > /tmp/backup-specs-20260901/$s.json
done

# 3. marcar as imagens em uso com o SHA delas, ANTES de construir
#    usando os SHAs lidos no passo 1

# 4. backup lógico do banco
pg_dump -Fc ... > appsell_native_90_pre0045_20260901T<hora>Z.dump
```

## Passo 1 — migrations 0045, 0046 e 0047

A imagem da `api` tem o Alembic mas **não tem as migrations**: só
`apps/api/src` entra no `Dockerfile.api`. Por isso o Alembic roda num contêiner
descartável, com o fonte montado do host.

```bash
docker run --rm --network container:<api> \
  -v <home-do-operador>/appsell-src-<sha>/apps/api:/work -w /work \
  -e APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_native_admin:<senha>@sell-autonomia_postgres-native-90:5432/appsell_native_90" \
  --entrypoint sh easypanel/sell-autonomia/api:<sha> \
  -c 'cd /work && python -m alembic upgrade head'
```

A variável é `APPSELL_MIGRATOR_DATABASE_URL`, não a da API. A senha de admin vem
do env do serviço `bootstrap-native-90`, lida em tempo de execução e nunca
escrita em disco.

O que cada migration faz:

- **0045** ensina o `confirm` que envio em partes não produz hash do arquivo
  inteiro. A conferência de conteúdo passa a ser a da verificação, que relê
  todos os bytes contra o mesmo hash da intenção. Quem decide se o envio foi
  fatiado é o **banco, pelo tamanho declarado**, nunca um sinalizador de quem
  chama.
- **0046** sobe o teto do vídeo em cinco pontos do banco e estende a validade da
  intenção de upload para duas horas quando o envio é fatiado.
- **0047** sobe a cota do vendedor de 1 GiB para 20 GiB, cerca de dezoito horas
  de curso, e passa a limitar também os **bytes em voo** e não só a quantidade
  de envios abertos. A cota vira função única em vez de número repetido em três
  lugares.

## Passo 2 — pdf-sandbox

O serviço mais delicado do conjunto, e o que este release obriga a mover.

> **Duas regras que já custaram produção.** Sempre `--no-healthcheck`, porque ele
> escuta num socket unix. E **nunca `start-first`**: um deploy assim apagou o
> socket, e todo PDF passou a abrir sem botão nenhum, sem erro na tela e sem
> nada no log (release `ee194bf`).

```bash
# a imagem do sandbox é a da api com outra tag; ver a seção do fim
docker tag easypanel/sell-autonomia/api:<sha> easypanel/sell-autonomia/pdf-sandbox:<sha>

docker service update --image .../pdf-sandbox:<sha> \
  --limit-cpu 4.0 --limit-memory 4G \
  --no-healthcheck --update-order stop-first sell-autonomia_pdf-sandbox
```

## Passo 3 — asset-worker

Compartilha a imagem da `api`. Depois de subir, conferir **de dentro do
contêiner** que `ffmpeg` e `pdftoppm` continuam presentes e que o `/tmp` tem os
3 GiB.

```bash
docker service update --image .../api:<sha> \
  --update-order stop-first sell-autonomia_asset-worker
```

## Passo 4 — web, e só depois api

Nesta ordem, e não na inversa: a tela nova funciona com a api velha, mas a api
nova quebra com a tela velha.

```bash
docker service update --image .../web:<sha> --update-order stop-first sell-autonomia_web
docker service update --image .../api:<sha> --update-order stop-first sell-autonomia_api
```

## Abortar e restaurar

Parar e voltar, **sem pedir nova autorização**, se qualquer uma destas
acontecer:

- o número de variáveis de ambiente de um serviço cair depois do update;
- qualquer serviço não ficar saudável dentro do tempo esperado;
- `schema_ready()` responder `false` depois da migration;
- o socket do sandbox sumir do volume compartilhado.

```bash
# voltar cada serviço para o SHA lido no passo 1 da preparação
docker service update --image .../api:<sha-anterior> sell-autonomia_api
docker service update --image .../web:<sha-anterior> sell-autonomia_web
docker service update --image .../api:<sha-anterior> sell-autonomia_asset-worker
docker service update --image .../pdf-sandbox:<sha-anterior> \
  --limit-cpu 4.0 --limit-memory 4G --no-healthcheck sell-autonomia_pdf-sandbox
```

**As migrations não precisam voltar.** São compatíveis com as imagens antigas, e a
guarda do `downgrade` recusa descer se houver dado que não caiba. A única parte que
aperta em vez de alargar é o teto de bytes em voo da 0047, e ele só morde um
vendedor com mais de 4 GiB parados em quarentena.

## Validação

Nenhum destes itens sozinho prova que o release funcionou. O que prova é o
último bloco.

### Banco

- [ ] `alembic current` responde `0047_cota_de_armazenamento`
- [ ] `schema_ready()` responde `true`
- [ ] `asset_media_max_bytes('video/mp4')` = `536870912`
- [ ] `asset_media_max_bytes('application/pdf')` = `104857600`, inalterado
- [ ] `public` **não** pode executar `asset_multipart_part_bytes()` nem
      `seller_storage_quota_bytes()`, e `appsell_runtime` pode
- [ ] `seller_storage_quota_bytes()` = `21474836480` (20 GiB) e
      `seller_open_upload_quota_bytes()` = `4294967296` (4 GiB)

### Serviços

- [ ] health da API, **de dentro do contêiner**: `database`, `storage` e
      `pdf_sandbox` verdadeiros
- [ ] health do BFF em `/api/health/ready`: `redis`, `api` e `storage_upload`
      verdadeiros
- [ ] worker com zero reinícios, `ffmpeg` e `pdftoppm` presentes, `/tmp` com
      3 GiB
- [ ] socket do sandbox presente no volume, e um PDF abrindo com os botões
- [ ] no log do worker **não** aparece `appsell_multipart_expiry_not_applied`

### A prova de verdade

- [ ] subir uma aula real acima de 16 MiB e ver a tela mandar em partes, com a
      barra andando
- [ ] cortar a rede no meio, voltar, e ver o envio **continuar** em vez de
      recomeçar
- [ ] o objeto guardado tem `moov` antes de `mdat` — é isso que faz a barra do
      aluno funcionar
- [ ] o audit do `confirm` registra `content_check = deferred_to_verification`
- [ ] o aluno abre a aula e arrasta a barra

## A receita do `pdf-sandbox`, respondida

Não existe `Dockerfile` do `pdf-sandbox`, e nunca existiu: **a imagem do sandbox
é a imagem da `api` com outra tag.** Quem faz o serviço virar sandbox é o
comando, que a spec dele sobrescreve para
`python -m appsell_api.pdf_sandbox_runner`.

Verificado no host em 01/09/2026, elo por elo:

| verificação | resultado |
|---|---|
| comando do serviço | `args=[python -m appsell_api.pdf_sandbox_runner]`, sobrescrevendo o `CMD` da imagem |
| a imagem da `api` importa o app do sandbox | sim: `AppSell isolated PDF parser` |
| `ffmpeg`, `ffprobe`, `pdftoppm` | presentes nas duas imagens, nos mesmos caminhos |
| `entrypoint` | vazio nas duas, então o comando do serviço substitui o padrão |
| uid/gid | 10001 nas duas, que é o que a checagem de dono do socket exige |
| `Dockerfile` removido do git em algum momento | nenhum |

A imagem em produção hoje (`712e5bb`) foi construída de um contexto diferente
(`--app-dir src`, contra `--app-dir apps/api/src` da atual). Isso não muda nada
aqui: o serviço troca o comando, e o `PYTHONPATH` da imagem nova já deixa o
módulo importável.

**Consequência para o passo 2:** não há imagem nova para construir. Basta marcar
a imagem da `api` com a tag do sandbox antes de atualizar o serviço.

## O que fica pendente depois do deploy

- **Acompanhar o disco da VPS.** A cota subiu de 1 GiB para 20 GiB por vendedor
  ([#335](https://github.com/autonom-ia/appsell/issues/335)), com o custo aceito
  de forma explícita. O disco é compartilhado com Manu, Lili e n8n: disco cheio
  não derruba só o Appsell. Vale medir o consumo antes que ele avise sozinho.
- **O buraco de CI**
  ([#333](https://github.com/autonom-ia/appsell/issues/333)): não existe passo de
  certificação da api no merge group, e o passo do PR ainda enumera arquivos à
  mão.
- Registrar este release em [PRODUCTION_CURRENT.md](../PRODUCTION_CURRENT.md),
  com os SHAs lidos do host e a validação executada, como os anteriores.

---

Preparado a partir do runbook dos releases `712e5bb`, `ee194bf` e `3524e2a`. Os
SHAs anteriores devem ser lidos do host, nunca deste documento: esta página já
nasce podendo estar desatualizada.
