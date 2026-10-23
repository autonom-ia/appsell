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
(`storage_internal_endpoint`), que não passa por esta borda.

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

1. Quem mais usa `minio.autonomia.site`: o destino `s3` do backup
   (`APPSELL_BACKUP_S3_ENDPOINT`), o `view-storage` e qualquer `mc` apontado
   para ele. Credencial em cabeçalho por esta borda passa a voltar 403.
   O bucket `appsell-backups` existe: se o backup `s3` grava nele **por
   este endereço**, passa a receber 403 e precisa do endpoint interno.
2. Logs do Traefik da última semana para o host `minio.autonomia.site`:
   método e caminho distintos. Qualquer coisa fora da tabela acima é um uso que
   esta mudança quebra: parar e reportar.

## Aplicação (só com OK do Rodrigo)

```sh
F=/etc/easypanel/traefik/config/appsell-storage.yaml
sha256sum "$F"        # tem de ser 551a0363…; outro valor: parar
: "${F:?caminho vazio — parar}"
cp -p -- "$F" "/tmp/appsell-storage-traefik_$(date -u +%Y%m%dT%H%M%SZ).yaml"   # backup
# escrever o novo arquivo a partir do SHA do merge, conferindo sha256 dos dois lados
```

O provedor de arquivo do Traefik recarrega sozinho. Conferir no log do Traefik
que não houve `error while parsing rule` nem `invalid rule`.

**Verificação, de fora do host:**

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://minio.autonomia.site/                    # 403
curl -s -o /dev/null -w '%{http_code}\n' https://minio.autonomia.site/minio/health/live   # 403
```

E na loja, nos primeiros 10 minutos: subir uma capa, um PDF pequeno e um vídeo
em partes num produto de teste, e abrir PDF e vídeo como comprador. Se qualquer
um falhar, rollback.

## Rollback

Pré-aprovado pelo mesmo OK da aplicação. Efeito imediato (recarga do arquivo):

```sh
cp -p -- /tmp/appsell-storage-traefik_<stamp>.yaml "$F"
```

ETA: menos de 1 minuto. Conferir `curl https://minio.autonomia.site/minio/health/live` → 200 de novo
e o envio na loja. Os serviços do Swarm não mudam: só o arquivo do Traefik.
