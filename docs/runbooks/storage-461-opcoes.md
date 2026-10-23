# Storage da produção depois do fim do MinIO community (#461)

Levantamento da trilha Plataforma, 23/10/2026. **A decisão é do Rodrigo.**
Nada aqui foi lido no host: a versão do MinIO em produção, o tamanho dos
buckets e a exposição real do endpoint ainda precisam de uma leitura no host,
com OK.

## O que mudou

| Fato | Como foi conferido |
|---|---|
| `minio/minio` arquivado no GitHub, último push 24/04/2026 | `gh api repos/minio/minio` → `archived: true` |
| Docker Hub `minio/minio` e `minio/mc` respondem 404 | API do Docker Hub, 23/10/2026 |
| `dl.min.io` responde 410 para servidor e para `mc` | `curl`, 23/10/2026 |
| Última release do GitHub com binário do servidor: `RELEASE.2025-09-07T16-13-09Z` | é a que o `cert-storage` baixa (#471) |
| Correções novas saem só no AIStor (pago) | `patched_versions` dos advisories abaixo |

## Risco hoje: há CVE de escrita sem autenticação sem correção livre

Advisories publicados em `github.com/minio/minio/security`, todos depois da
última release community com binário:

| CVE | Gravidade | O que é | Afeta | Corrigido em |
|---|---|---|---|---|
| CVE-2026-41145 | alta | escrita de objeto **sem autenticação** em upload com *unsigned trailer* | ≥ 2023-05-18 | 2026-04-11 (pós-arquivamento) |
| CVE-2026-40344 | alta | escrita de objeto **sem autenticação** pelo auto-extract do Snowball | ≥ 2023-05-18 | 2026-04-11 |
| CVE-2026-34204 | alta | injeção de metadado SSE por cabeçalho de replicação | > 2024-03-30 | 2026-03-26 |
| CVE-2026-39414 | alta | DoS por memória no S3 Select CSV | todas | só AIStor |
| CVE-2026-42600 | média | path traversal no storage-REST | ≥ 2022-07-24 | 2026-04-14 |
| CVE-2026-33322 / 33419 | média | OIDC JWT / brute-force LDAP | — | 2026-03-17 |

Qualquer versão community que a produção rode está nas faixas das duas
primeiras. O repositório publica o storage em `minio.autonomia.site` pelo
Traefik (`deploy/easypanel-native/appsell-storage.traefik.yaml`), porque o
navegador envia direto ao bucket por URL assinada. **Se a produção estiver
igual ao repositório, o endpoint vulnerável está na internet.** Isso precisa de
confirmação no host antes de qualquer outra coisa.

Segundo afetado: o backup off-host `s3` usa `minio/mc:latest`
(`deploy/easypanel-native/backup/lib-backup.sh`). Essa imagem não existe mais
no Docker Hub. Um host sem a imagem em cache não consegue rodar esse destino.

## O que o Sell exige do storage

Lido em `apps/api/src/appsell_api/storage_s3.py` e nos contratos de teste:

- URL assinada para `PUT` com `x-amz-checksum-sha256`, tamanho e metadados fixos;
- multipart com checksum por parte (`create`/`list_parts`/`complete`/`abort`, #460);
- **versionamento**: o worker exige `VersionId` em todo objeto, e o banco
  guarda o id da versão. São 104 referências a `version_id` em 10 módulos;
- CORS restrito à origem da loja;
- lifecycle (`put_bucket_lifecycle_configuration`) para limpar quarentena;
- `copy_object`, `list_object_versions`, `delete_objects`.

O versionamento é o filtro que decide as opções.

## Opções

### A. Ficar no MinIO community atual, risco aceito

- **Custo:** zero.
- **Esforço:** zero, mais mitigação no Traefik se der (bloquear o que as CVEs
  usam: `x-amz-content-sha256: STREAMING-UNSIGNED-PAYLOAD-TRAILER` com
  credencial na query, e `X-Amz-Meta-Snowball-Auto-Extract`). A mitigação
  precisa ser provada contra o próprio fluxo do Sell, que usa URL assinada.
- **Risco:** alto e crescente. Duas escritas sem autenticação já públicas, sem
  correção possível. Cada CVE nova vai para a mesma lista.

### B. Trocar pelo Silo (fork mantido do MinIO, `pgsty/silo`)

- **O que é:** fork AGPL-3.0 do servidor MinIO, mantido pelo Pigsty. Releases
  em 03/09, 16/09 e 18/10/2026, imagem `docker.io/pgsty/silo` multi-arch,
  publica advisory a cada correção. Diz preservar formato de dados, variáveis
  `MINIO_*`, rotas `/minio/*` e `.minio.sys`.
- **Custo:** zero de licença; mesma VPS.
- **Esforço:** baixo. Trocar a imagem do serviço `sell-autonomia_storage` (e do
  `view-storage`), mantendo o volume. O binário se chama `silo`, não `minio`:
  o comando do serviço muda. O `mc` vira `mcli` dentro da imagem, o que resolve
  o backup também. Rodar o `cert-storage` contra o Silo antes (o
  `test-storage-minio.sh` troca de binário em poucas linhas).
- **Risco:** médio. Um mantenedor principal; projeto de 2026. A troca em
  produção mexe no storage da loja: precisa de snapshot do volume, janela, e
  rollback para a imagem atual com o mesmo volume. Conferir antes que o Silo
  lê o formato da versão de produção (por isso a versão precisa ser lida).
- **Sem mudança de código no Sell.**

### C. Migrar para o SeaweedFS

- **O que é:** já roda no `cert-storage` e passa no contrato inteiro do Sell,
  versionamento e lifecycle incluídos.
- **Custo:** zero de licença; mesma VPS.
- **Esforço:** médio. Serviço novo, cópia de todos os objetos **com as
  versões** (o banco aponta para `VersionId`; uma cópia comum gera ids novos e
  quebra a referência), reescrita dos ids no banco ou cópia que preserve
  versões, troca de credencial, de CORS e do backup.
- **Risco:** médio-alto, concentrado na migração de dados e na reescrita de
  `version_id`. O comportamento já é provado no CI, o que reduz o risco do
  depois.

### D. Migrar para o Garage

- **Bloqueado.** O Garage não implementa versionamento de objeto (lista
  "Not implemented" na página de compatibilidade; issue #166 aberta desde 2021).
  Exigiria remover a dependência de `VersionId` do Sell inteiro.

### E. S3 gerenciado

- **Cloudflare R2: bloqueado** pelo mesmo motivo. A tabela de compatibilidade
  marca `GetBucketVersioning` como não implementado.
- **AWS S3:** cobre tudo que o Sell usa. Custo por GB guardado e, principalmente,
  por GB servido (vídeo e PDF ao aluno saem do bucket). Sem o tamanho dos
  buckets e o tráfego de saída não dá para estimar; os dois vêm do host.
  Esforço de migração igual ao da opção C (versões e ids), mais conta, IAM e
  credencial: decisão de billing e infra.

## Recomendação da trilha

1. **Agora:** ler no host, com OK, a versão do MinIO, se `minio.autonomia.site`
   responde de fora, e o tamanho dos buckets. Sem isso nenhuma opção fecha.
2. **Se o endpoint estiver público:** opção B como correção, porque é a única
   que fecha as CVEs sem migrar dado nem mudar código. Antes, `cert-storage`
   contra o Silo no CI.
3. **Depois, sem pressa:** decidir entre ficar no Silo ou migrar (C ou AWS S3),
   olhando a saúde do fork em alguns meses.

Opção A só como ponte curta, com a mitigação no Traefik provada.

## Fontes

- Advisories: <https://github.com/minio/minio/security>
- Silo: <https://github.com/pgsty/minio> (README e releases)
- Garage, compatibilidade S3: <https://garagehq.deuxfleurs.fr/reference_manual/s3_compatibility.html>
  e <https://git.deuxfleurs.fr/Deuxfleurs/garage/issues/166>
- R2, compatibilidade S3: <https://developers.cloudflare.com/r2/api/s3/api/>
