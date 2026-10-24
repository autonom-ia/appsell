# Borda do storage só com o que o navegador usa (#461)

Mitigação, não correção. O MinIO community não recebe mais correção de
segurança, e `minio.autonomia.site` responde na internet (conferido de fora pela
Orquestração em 23/10/2026: `server: MinIO`, `/minio/health/live` 200). Esta
mudança reduz o que chega ao MinIO por essa borda. A troca do servidor está no
`storage-461-opcoes.md`.

**Nada aqui foi aplicado.** A aplicação é escrita em produção e precisa de OK do
Rodrigo.

## O que o navegador usa

Lido no código (`apps/api/src/appsell_api/storage_s3.py`,
`lib/storage-upload-policy.server.ts`). Tudo vai por URL pré-assinada, com a
assinatura na query:

| Método | Caminho | Quem gera | Para quê |
|---|---|---|---|
| `PUT` | `/<quarentena>/<chave>` | `presign_put`, `presign_cover_put` | envio simples e capa |
| `PUT` | `/<quarentena>/<chave>?partNumber&uploadId` | `presign_upload_part` | parte do multipart |
| `GET` | `/<pronto>/<chave>?versionId` | `presign_get`, `presign_cover` | PDF, vídeo (com `Range`), imagem, capa |
| `OPTIONS` | qualquer | navegador | preflight do CORS |

Cabeçalhos que o navegador manda: `content-type`, `x-amz-checksum-sha256`,
`x-amz-meta-appsell-upload-id`. Criar, fechar e abortar multipart, `HEAD`,
cópia e remoção são da API e do worker, pelo endpoint **interno**
(`storage_internal_endpoint`). Ele só não passa por esta borda se apontar para a
rede do Swarm. Na produção, em 23/10/2026, ele passava: ver "Antes de
aplicar", item 0.

O `storage_public_audit` (rodado à mão, de fora) faz OPTIONS em `/` e sondas
anônimas que precisam voltar 401 ou 403. Continua passando.

## O que muda

`deploy/easypanel-native/appsell-storage.traefik.yaml`:

- o roteador `appsell-storage-https` só casa `PUT` na quarentena, `GET` no
  pronto e `OPTIONS`;
- novo `appsell-storage-only-query-auth` remove `Authorization`,
  `X-Amz-Content-Sha256` e `X-Amz-Meta-Snowball-Auto-Extract`: nenhuma
  credencial em cabeçalho chega ao MinIO por esta borda, nem o modo de envio com
  trailer não assinado, nem a extração automática de tar;
- novo roteador `appsell-storage-deny`, prioridade **99**, responde 403 a todo
  o resto: `ListBuckets`, `/minio/admin`, `/minio/health`, `DELETE`, `POST`,
  `HEAD`, escrita no pronto, leitura da quarentena.

**Por que 99, e não 1.** O `main.yaml` que o EasyPanel gera tem
`https-dados_minio-0` para o mesmo host, `priority: 0` (no Traefik, o tamanho da
regra: 46), apontando para `dados_minio:9001`, o console do MinIO do projeto
`dados`. Hoje ela nunca ganha, porque a rota do Sell tem 100. Um bloqueio com
prioridade abaixo de 46 mandaria o pedido negado para esse console. O ensaio
abaixo provou isso.

## Ensaio (local, 23/10/2026)

Traefik **3.6.7**, a versão da produção, e 3.7.16 (binários da release,
SHA-256 conferido), com este arquivo, uma cópia da rota `https-dados_minio-0`
(prioridade 0) apontando para uma isca que responde 299, e um MinIO
`RELEASE.2025-09-07T16-13-09Z` local, a versão da produção. URLs assinadas com boto3, pedidos
com `curl --resolve minio.autonomia.site:18443:127.0.0.1`. **17/17**:

- passam: PUT pré-assinado com os cabeçalhos do navegador (200, e o objeto
  existe), parte de multipart (200), GET com `versionId` (200) e com `Range`
  (206), preflight em objeto e em `/` (200, `allow-origin` só para
  `https://sell.autonomia.site`);
- 403: `GET /`, `/minio/health/live`, `/minio/admin/v3/info`, DELETE
  pré-assinado, POST `?uploads`, HEAD, PUT no pronto, GET na quarentena, PUT e
  ListObjects com SigV4 **no cabeçalho** (controle: o mesmo pedido direto no
  MinIO passa), PUT anônimo.

Controle: com o bloqueio em prioridade 1, 8 dos pedidos que deviam dar 403
chegaram à isca (9/17). Com 99, 17/17 nas duas versões do Traefik.

Com `Query(X-Amz-Algorithm, AWS4-HMAC-SHA256)` nas rotas de PUT e GET
(MÉDIO da Qualidade), **22/22** no 3.6.7, com a isca no ar: PUT e GET sem
assinatura, com assinatura forjada e com outro algoritmo voltam 403, e nenhum
chega à isca.

O ensaio achou três erros das versões anteriores, já corrigidos:

- regra quebrada em várias linhas: o Traefik recusa (`expected ')', found newline`);
- `ipAllowList` com `127.0.0.1/32` deixava passar pedido vindo do próprio host.
  A faixa agora é `192.0.2.1/32` (TEST-NET-1), que nunca é origem real;
- bloqueio com prioridade 1 perdia para a rota do `dados`.

## Leitura no host (23/10/2026, com OK, só leitura)

- Traefik: serviço `easypanel-traefik`, imagem `traefik:3.6.7`.
- Arquivo ativo: `/etc/easypanel/traefik/config/appsell-storage.yaml`, com
  SHA-256 `551a0363…` **igual** ao `appsell-storage.traefik.yaml` da `main`. Na
  mesma pasta há `appsell-storage.yaml.bak-20260822T1121Z`, que o provedor de
  arquivo não carrega por causa da extensão.
- Buckets no volume `sell-autonomia_storage_data`: `appsell-quarantine` (4 KB),
  `appsell-ready` (196 MB) e `appsell-backups` (152 KB), os nomes do
  `PathPrefix`.
- MinIO: `RELEASE.2025-09-07T16-13-09Z`.

## Antes de aplicar

0. **Bloqueante.** O `storage_internal_endpoint` da `api` e do `asset-worker` não
   pode passar por `minio.autonomia.site`. Em 23/10/2026 a borda foi aplicada e
   revertida em 38 s porque passava: o `HEAD` de controle da api fez hairpin
   pelo Traefik, caiu no bloqueio, e o `/health/ready` foi a 503 (#461). O
   endpoint precisa apontar para a rede interna do Swarm antes de reaplicar. As
etapas 1 a 6b do `storage-461-rede-interna.md` vêm antes desta.

   Conferência (só leitura, OK próprio). Imprime **só o hostname** de cada
   endpoint, nunca o valor inteiro nem outra variável:

   ```sh
   for s in sell-autonomia_api sell-autonomia_asset-worker; do
     sudo -n docker service inspect "$s" --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' \
       | python3 -c '
   import json, sys, urllib.parse
   env = dict(e.split("=", 1) for e in json.load(sys.stdin) if "=" in e)
   for nome in ("APPSELL_API_STORAGE_INTERNAL_ENDPOINT", "APPSELL_API_STORAGE_PUBLIC_ENDPOINT"):
       valor = env.get(nome)
       print(sys.argv[1], nome, urllib.parse.urlsplit(valor).hostname if valor else "ausente")
   ' "$s"
   done
   ```

   Esperado: `INTERNAL_ENDPOINT` = `sell-autonomia-storage` nos dois serviços.
   É o alias que o EasyPanel já dá ao `storage` na rede do projeto
   (`easypanel-sell-autonomia`), e entra pelas etapas 6a e 6b do
   `storage-461-rede-interna.md`. `storage` **não** serve: a api também está na
   rede compartilhada `easypanel`, onde o nome pode ser de outro projeto.
   `sell-autonomia_storage` também não: o MinIO responde 400 a Host com `_`;
   `PUBLIC_ENDPOINT` = `minio.autonomia.site` na api. No worker, ausente ou o
   mesmo valor. `INTERNAL_ENDPOINT` = `minio.autonomia.site`: **não aplicar**.
1. Quem mais usa `minio.autonomia.site`: o destino `s3` do backup
   (`APPSELL_BACKUP_S3_ENDPOINT`), o `view-storage` e qualquer `mc` apontado
   para ele. Credencial em cabeçalho por esta borda passa a voltar 403.
   O bucket `appsell-backups` existe: se o backup `s3` grava nele **por
   este endereço**, passa a receber 403 e precisa do endpoint interno.
2. Logs do Traefik da última semana para o host `minio.autonomia.site`:
   método e caminho distintos. Qualquer coisa fora da tabela acima é um uso que
   esta mudança quebra: parar e reportar.

## Aplicação (só com OK do Rodrigo)

Por que atômica: o Traefik relê a pasta a cada mudança. Um arquivo meio escrito
ou inválido some com a rota `appsell-storage-https`, e o host inteiro passa a
cair em `https-dados_minio-0`, o console do MinIO do projeto `dados`. Por isso
o arquivo novo é escrito ao lado, validado, e só então entra com `mv`, que no
mesmo sistema de arquivos é uma troca única.

O nome temporário não termina em `.yaml`, `.yml` nem `.toml`: o provedor de
arquivo só lê essas extensões, então ele não é carregado enquanto espera.

Os caminhos são literais. `<sha-do-merge>` e `<sha256-do-arquivo-no-merge>`
são escritos antes da janela. Hoje, no head do PR #477, o SHA-256 do arquivo é
`20a24fa30cc6577234bf3361893c6373431b157e6bf4e5eab929c0830aada09e`; se o merge
mudar o arquivo, vale o do merge.

```sh
# 0. sudo sem senha; sem ele, parar antes de tocar em qualquer coisa
sudo -n true || { echo "PARAR: sudo -n não liberado neste host"; exit 1; }

# 0b. linha de base: rodar AGORA o bloco do passo 7, antes de qualquer troca.
#     Prova que o `python -c` funciona na api e no worker. Se falhar aqui, o
#     problema não é a borda: parar e reportar.

# 1. estado atual: o arquivo ativo tem de ser o de hoje
sudo -n sha256sum /etc/easypanel/traefik/config/appsell-storage.yaml
#    → 551a0363d6b8b6e96a474174e1fc8e6f11f8d7abc605bb80d3bb02149453347b; outro valor: parar

# 2. backup com nome fixo, fora da pasta do Traefik
test ! -e /tmp/appsell-storage_traefik_pre477.yaml      # existir: parar e perguntar
sudo -n cp -p -- /etc/easypanel/traefik/config/appsell-storage.yaml /tmp/appsell-storage_traefik_pre477.yaml
sudo -n sha256sum /tmp/appsell-storage_traefik_pre477.yaml      # → 551a0363…

# 3. o novo, escrito ao lado com nome que o Traefik não lê
#    (no Mac, num checkout com o merge:)
#    git show <sha-do-merge>:deploy/easypanel-native/appsell-storage.traefik.yaml > /tmp/appsell-storage_traefik_477.novo
#    scp /tmp/appsell-storage_traefik_477.novo claudete:/tmp/appsell-storage_traefik_477.novo
#    (no host:)
sudo -n cp -- /tmp/appsell-storage_traefik_477.novo /etc/easypanel/traefik/config/.appsell-storage.novo
sudo -n chmod --reference=/etc/easypanel/traefik/config/appsell-storage.yaml /etc/easypanel/traefik/config/.appsell-storage.novo
sudo -n chown --reference=/etc/easypanel/traefik/config/appsell-storage.yaml /etc/easypanel/traefik/config/.appsell-storage.novo

# 4. validação antes do mv
sudo -n sha256sum /etc/easypanel/traefik/config/.appsell-storage.novo   # → <sha256-do-arquivo-no-merge>; outro: apagar o .novo e parar
python3 -c 'import sys, yaml; yaml.safe_load(open(sys.argv[1]))' /etc/easypanel/traefik/config/.appsell-storage.novo \
  && echo "yaml ok"   # se o host não tiver PyYAML, vale o sha256: o mesmo arquivo passou no ensaio (22/22)
grep -c 'appsell-storage-https\|appsell-storage-deny' /etc/easypanel/traefik/config/.appsell-storage.novo   # → 4

# 5. troca única
sudo -n mv -f -- /etc/easypanel/traefik/config/.appsell-storage.novo /etc/easypanel/traefik/config/appsell-storage.yaml

# 6. o Traefik aceitou?
sleep 5
sudo -n docker service logs --since 1m easypanel-traefik 2>&1 | grep -i 'error while parsing rule\|invalid rule\|level=error\|"level":"error"' \
  | grep -i -c 'appsell-storage\|providerName=file\|"providerName":"file"'
#    → 0. O primeiro filtro pega só erro (o log de acesso traz o nome do
#    roteador em toda linha); o segundo deixa só o que é deste arquivo ou do
#    provedor de arquivo, não erro de outro projeto da VPS. Os dois filtros
#    aceitam o log em texto e em JSON: o de acesso lido em 23/10/2026 era texto,
#    mas o de erro nunca foi visto.

# 7. api e worker continuam vendo o storage. O probe roda direto em cada
#    contêiner, sem o cache do /health/ready (que vale até 30 s e mostraria o
#    resultado de antes da troca).
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  cid=$(sudo -n docker inspect --format '{{.Status.ContainerStatus.ContainerID}}' \
    "$(sudo -n docker service ps -q "$s" --filter desired-state=running | head -1)")
  sudo -n docker exec "$cid" python -c '
from appsell_api.config import get_settings
from appsell_api.storage_s3 import S3Storage
S3Storage(get_settings()).probe_availability()
print("storage ok")' && echo "$s ok" || echo "$s FALHOU: rollback imediato"
done
#    E o /health/ready, três leituras com 15 s entre elas, todas com
#    "storage":true:
cid=$(sudo -n docker ps -q --filter name=sell-autonomia_api. | head -1)
for _ in 1 2 3; do
  sleep 15
  sudo -n docker exec "$cid" python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=5).read().decode())'
done
#    503, erro ou "storage":false em qualquer uma: rollback imediato
```

**Verificação, de fora do host:**

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://minio.autonomia.site/                    # 403
curl -s -o /dev/null -w '%{http_code}\n' https://minio.autonomia.site/minio/health/live   # 403
curl -s -o /dev/null -w '%{http_code}\n' https://minio.autonomia.site/minio/login         # 403, não a tela do console do dados
```

E na loja, nos primeiros 10 minutos: subir uma capa, um PDF pequeno e um vídeo
em partes num produto de teste, e abrir PDF e vídeo como comprador. Se qualquer
um falhar, rollback.

## Rollback

Pré-aprovado pelo mesmo OK da aplicação. Também atômico, com caminhos literais:

```sh
sudo -n true || { echo "PARAR: sudo -n não liberado neste host"; exit 1; }
sudo -n cp -p -- /tmp/appsell-storage_traefik_pre477.yaml /etc/easypanel/traefik/config/.appsell-storage.volta
sudo -n sha256sum /etc/easypanel/traefik/config/.appsell-storage.volta   # → 551a0363…
sudo -n mv -f -- /etc/easypanel/traefik/config/.appsell-storage.volta /etc/easypanel/traefik/config/appsell-storage.yaml
sudo -n sha256sum /etc/easypanel/traefik/config/appsell-storage.yaml     # → 551a0363…
```

ETA: menos de 1 minuto. Conferir `curl https://minio.autonomia.site/minio/health/live` → 200 de novo
e o envio na loja. Os serviços do Swarm não mudam: só o arquivo do Traefik.

Depois de 24 h estáveis, `/tmp/appsell-storage_traefik_pre477.yaml` é apagado
pelo nome exato.
