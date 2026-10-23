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
- novo roteador `appsell-storage-deny`, prioridade 1, responde 403 a todo o
  resto: `ListBuckets`, `/minio/admin`, `/minio/health`, `DELETE`, `POST`,
  `HEAD`, escrita no pronto, leitura da quarentena.

## Ensaio (local, 23/10/2026)

Traefik **3.7.16** (binário da release, SHA-256 conferido) com este arquivo e um
MinIO `RELEASE.2025-09-07T16-13-09Z` local. URLs assinadas com boto3, pedidos
com `curl --resolve minio.autonomia.site:18443:127.0.0.1`. **17/17**:

- passam: PUT pré-assinado com os cabeçalhos do navegador (200, e o objeto
  existe), parte de multipart (200), GET com `versionId` (200) e com `Range`
  (206), preflight em objeto e em `/` (200, `allow-origin` só para
  `https://sell.autonomia.site`);
- 403: `GET /`, `/minio/health/live`, `/minio/admin/v3/info`, DELETE
  pré-assinado, POST `?uploads`, HEAD, PUT no pronto, GET na quarentena, PUT e
  ListObjects com SigV4 **no cabeçalho** (controle: o mesmo pedido direto no
  MinIO passa), PUT anônimo.

O ensaio achou dois erros da primeira versão, já corrigidos:

- regra quebrada em várias linhas: o Traefik recusa (`expected ')', found newline`);
- `ipAllowList` com `127.0.0.1/32` deixava passar pedido vindo do próprio host.
  A faixa agora é `192.0.2.1/32` (TEST-NET-1), que nunca é origem real.

## Antes de aplicar (leitura no host, com OK)

1. Versão do Traefik: `docker service inspect easypanel-traefik --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'`.
   O arquivo usa a sintaxe v3 (`ipAllowList`; regras v3). Em v2 **não aplicar**.
2. Onde o arquivo vive: procurar o roteador `appsell-storage-https` nos
   arquivos de configuração dinâmica do Traefik do EasyPanel. Se não estiver
   lá, a borda atual é outra, e este runbook para.
3. Nomes dos buckets: `APPSELL_API_STORAGE_QUARANTINE_BUCKET` e
   `APPSELL_API_STORAGE_READY_BUCKET` da api, **só os nomes**, nunca outros valores. Se
   diferirem de `appsell-quarantine` e `appsell-ready`, trocar no `PathPrefix`.
4. Quem mais usa `minio.autonomia.site`: o destino `s3` do backup
   (`APPSELL_BACKUP_S3_ENDPOINT`), o `view-storage` e qualquer `mc` apontado
   para ele. Credencial em cabeçalho por esta borda passa a voltar 403.
5. Logs do Traefik da última semana para o host `minio.autonomia.site`:
   método e caminho distintos. Qualquer coisa fora da tabela acima é um uso que
   esta mudança quebra: parar e reportar.

## Aplicação (só com OK do Rodrigo)

```sh
F=<caminho lido no passo 2>
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
