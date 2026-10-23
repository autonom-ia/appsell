# Storage da produção depois do fim do MinIO community (#461)

Levantamento da trilha Plataforma, 23/10/2026. **A decisão é do Rodrigo.**
Nada aqui foi lido no host. A versão do MinIO em produção e o tamanho dos
buckets ainda precisam de uma leitura no host, com OK. A exposição foi
conferida de fora pela Orquestração: `minio.autonomia.site` responde na
internet (`server: MinIO`, `/minio/health/live` 200).

## O que mudou

| Fato | Como foi conferido |
|---|---|
| `minio/minio` arquivado no GitHub, último push 24/04/2026 | `gh api repos/minio/minio` → `archived: true` |
| Docker Hub `minio/minio` e `minio/mc` respondem 404 | API do Docker Hub, 23/10/2026 |
| `dl.min.io` responde 410 para servidor e para `mc` | `curl`, 23/10/2026 |
| Última release do GitHub com binário do servidor: `RELEASE.2025-09-07T16-13-09Z` | é a que o `cert-storage` baixa (#471) |
| Correções novas saem só no AIStor (pago) | `patched_versions` dos advisories abaixo |

## Risco hoje: advisories sem correção livre

Advisories publicados em `github.com/minio/minio/security`, todos depois da
última release community com binário. **Se cada um se aplica à produção
depende da versão e da configuração lidas no host**; a tabela é a faixa que o
advisory declara, não uma conclusão sobre o nosso servidor.

| CVE | Gravidade | O que é | Afeta | Corrigido em |
|---|---|---|---|---|
| CVE-2026-41145 | alta | escrita de objeto **sem autenticação** em upload com *unsigned trailer* | ≥ 2023-05-18 | 2026-04-11 (pós-arquivamento) |
| CVE-2026-40344 | alta | escrita de objeto **sem autenticação** pelo auto-extract do Snowball | ≥ 2023-05-18 | 2026-04-11 |
| CVE-2026-34204 | alta | injeção de metadado SSE por cabeçalho de replicação | > 2024-03-30 | 2026-03-26 |
| CVE-2026-39414 | alta | DoS por memória no S3 Select CSV | todas | só AIStor |
| CVE-2026-42600 | média | path traversal no storage-REST | ≥ 2022-07-24 | 2026-04-14 |
| CVE-2026-33322 / 33419 | média | OIDC JWT / brute-force LDAP | — | 2026-03-17 |

O storage é publicado pelo Traefik
(`deploy/easypanel-native/appsell-storage.traefik.yaml`), porque o navegador
envia direto ao bucket por URL assinada. A mitigação de borda (só o que o
navegador usa passa) está no `storage-461-borda-traefik.md` e não substitui a
troca do servidor.

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
- **Esforço:** zero além da borda no Traefik (`storage-461-borda-traefik.md`,
  ensaiada contra o fluxo do Sell: 17/17).
- **Risco:** alto e crescente. Há advisories de escrita sem autenticação sem
  correção livre; se pegam a nossa versão, a leitura no host diz. Cada advisory
  novo vai para a mesma lista, e a borda só reduz a superfície.

### B. Trocar pelo Silo (fork mantido do MinIO, `pgsty/silo`)

- **O que é:** fork AGPL-3.0 do servidor MinIO, mantido pelo Pigsty. Releases
  em 03/09, 16/09 e 18/10/2026, imagem `docker.io/pgsty/silo` multi-arch,
  publica advisory a cada correção. Diz preservar formato de dados, variáveis
  `MINIO_*`, rotas `/minio/*` e `.minio.sys`.
- **Custo:** zero de licença; mesma VPS.
- **Esforço:** baixo. Troca a imagem no **mesmo volume**, sem copiar dado. Ver
  o plano abaixo.
- **Risco:** médio. Um mantenedor principal; projeto de 2026. A troca mexe no
  storage da loja: precisa de snapshot, janela curta e rollback ensaiado.
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

## Plano do Silo

### O ensaio que já rodou (local, 23/10/2026)

Binários conferidos por SHA-256: MinIO `RELEASE.2025-09-07T16-13-09Z` e Silo
`RELEASE.2026-10-18T00-00-00Z` (darwin-arm64). Um diretório de dados só:

1. **MinIO grava:** buckets versionados, um objeto com duas versões, um
   multipart fechado com checksum por parte (5 MiB + 3 B) e um multipart **em
   andamento** com uma parte.
2. **Silo sobe no mesmo diretório:** as 3 versões com os **mesmos `VersionId`,
   ETag e tamanho**. `GET` por `VersionId` devolve cada versão. O multipart
   fechado bate no SHA-256. O em andamento lista a parte com o mesmo
   `ChecksumSHA256`.
3. **Contrato do Sell contra o Silo:** `test_storage_s3_contract.py -k
   "multipart_round_trip or presign_rejects"`, o mesmo passe MinIO do
   `cert-storage` (#471, `a693c12`): **2 passed**.
4. **Rollback:** o Silo grava um objeto novo; o MinIO volta no mesmo
   diretório, confere tudo do passo 2 e lê o objeto do Silo.

Ou seja, para o caso ensaiado: troca no lugar, sem cópia, e o `VersionId` que o
banco guarda continua válido. O que o ensaio **não** cobre: a versão de
produção (pode ser anterior à de 2025-09), o layout de disco de lá e o volume
real. Por isso o passo 1 abaixo.

### Na produção (cada escrita com OK do Rodrigo)

1. **Leitura no host:** versão do MinIO, modo (um disco ou vários), tamanho do
   volume, espaço livre, e quem usa `minio.autonomia.site` além do navegador.
2. **CI:** o `test-storage-minio.sh` ganha um segundo passe contra o Silo
   linux-amd64 (`silo_20261018000000.0.0_linux_amd64.tar.gz`, SHA-256
   `e069b6425f0de944fea6c301773d5620fe8992bf0d16b6c5e42be57d666ee794`). PR
   próprio.
3. **Ensaio com cópia dos dados de produção**, fora do host, se o volume
   couber; senão, com a versão exata de produção gerando os dados, como acima.
4. **Snapshot:** do volume (ou da VPS, pela Hostinger), antes da janela.
5. **Troca:** `docker service update --image pgsty/silo@<digest>` nos dois
   serviços (`storage` e `view-storage`), `--update-order stop-first`, mesmo
   volume, mesmas variáveis `MINIO_*`. O comando muda de `minio server` para
   `silo server`. As rotas `/minio/health/*` continuam (README do Silo).
6. **Verificação:** health, `storage_public_audit` de fora, e na loja um envio
   de capa, PDF e vídeo em partes, mais a leitura de PDF e vídeo como comprador.
7. **Rollback:** `docker service update --image <imagem MinIO atual>` no mesmo
   volume, como no passo 4 do ensaio. Menos de 2 minutos.

### Backup: `minio/mc:latest` deixou de existir

O destino `s3` do backup (`deploy/easypanel-native/backup/lib-backup.sh`) usa
`minio/mc:latest` (404 no Docker Hub). Proposta, em PR próprio:

- padrão do `BACKUP_MC_IMAGE` passa a ser a imagem do Silo **por digest**:
  `pgsty/silo@sha256:2636fc4155d9a7e41ff58cdb65ddd13ba9304e67184e13c25bc5cb34016269d7`
  (tag `RELEASE.2026-10-18T00-00-00Z`; amd64
  `sha256:706e0d419d23bdab663486044165fb4de1ee6b82885a8637b2935216fb2286ec`);
- o cliente nessa imagem se chama `mcli`: `--entrypoint mcli`;
- **a conferir no PR:** se o `mcli` lê `MC_HOST_offhost` como o `mc` (o
  `lib-backup.sh` passa a credencial por essa variável, pelo nome);
- o teste de CI do backup (`local-dir`) não cobre o `s3`. O PR precisa de um
  ensaio do `s3` contra o MinIO/Silo do CI.

## Recomendação da trilha

1. **Agora:** aplicar a borda (`storage-461-borda-traefik.md`) e ler no host,
   com OK, a versão do MinIO e o tamanho dos buckets.
2. **Em seguida:** opção B, porque é a única que sai da linha sem correção sem
   migrar dado nem mudar código, e a troca no lugar já foi ensaiada. Antes,
   `cert-storage` contra o Silo no CI.
3. **Depois, sem pressa:** decidir entre ficar no Silo ou migrar (C ou AWS S3),
   olhando a saúde do fork em alguns meses.

Opção A só como ponte curta, com a mitigação no Traefik provada.

## Fontes

- Advisories: <https://github.com/minio/minio/security>
- Silo: <https://github.com/pgsty/minio> (README e releases)
- Garage, compatibilidade S3: <https://garagehq.deuxfleurs.fr/reference_manual/s3_compatibility.html>
  e <https://git.deuxfleurs.fr/Deuxfleurs/garage/issues/166>
- R2, compatibilidade S3: <https://developers.cloudflare.com/r2/api/s3/api/>
