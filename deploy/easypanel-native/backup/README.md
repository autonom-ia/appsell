# Backup lógico cifrado — Autonom.ia Sell (native-90)

Scripts de backup/restore do banco `appsell_native_90` (serviço EasyPanel
`postgres-native-90`, projeto `sell-autonomia`). O release de produção possui
um dump pré-cutover preservado; ainda não há evidência de rotina recorrente
diária/PITR concluída. Este diretório entrega a primeira camada: dump lógico
diário, cifrado e enviado off-host, com restore ensaiável. PITR/WAL fica documentado como evolução
no runbook (`docs/runbooks/BACKUP_RESTORE_SELL.md`), pois exige acesso ao volume
do Postgres nativo (decisão de infra).

> Nada aqui toca a VPS sozinho. São scripts prontos para execução aprovada.
> O runbook marca cada passo 🔴 que precisa de OK explícito do Rodrigo.

## Arquivos

| Arquivo | Função |
|---|---|
| `lib-backup.sh` | Helpers compartilhados: validação de env, wrappers do client Postgres (dump/restore/verify), cifra/decifra gpg, rotação local, envio off-host. Só define funções. |
| `backup-native.sh` | Backup diário: `pg_dump -Fc` → integridade (`pg_restore --list`) → cifra AES256 → off-host → rotação (7 diários). |
| `restore-native.sh` | Restore **ensaiável** de um dump cifrado em Postgres descartável. Nunca toca produção. |
| `test-backup-native.sh` | Rehearsal de CI (nível biblioteca): sobe 2 Postgres descartáveis, semeia dados, dump, cifra, envia off-host, decifra a cópia off-host, restaura no segundo banco e compara por fingerprint. |
| `test-entrypoints-native.sh` | Rehearsal de CI (nível processo): executa `backup-native.sh` e `restore-native.sh` **como subprocessos reais** — valida o contrato de env vars, a limpeza do dump em claro via `trap` e o resumo de verificação do restore. |

## Princípios de segurança

- **Zero credencial hardcoded.** Todo segredo chega por variável de ambiente.
- **Nenhum segredo em argv.** O `argv` dos processos `docker`/`gpg` do host é
  legível por qualquer usuário via `/proc/<pid>/cmdline` (e `ps`). Por isso:
  - a passphrase gpg vai por **file descriptor** (`--passphrase-fd 3`), nunca em
    argv e nunca em arquivo;
  - segredos de container (`PGPASSWORD`, credenciais S3 em `MC_HOST_offhost`) são
    repassados ao container **por nome** (`docker run -e VAR`, sem `=valor`), com
    o valor exportado só naquela invocação. O Docker lê o valor do próprio
    ambiente (`/proc/<pid>/environ`, modo `0400` — só dono e root), nunca do argv.
    Usar `-e VAR=valor` é proibido nestes scripts (vazaria no argv).
- O client Postgres roda em **container fixado** (`postgres:16-alpine`); o host só
  precisa de Docker + gpg. Versão de servidor casada pela tag da imagem.
- O dump em claro é apagado assim que o artefato cifrado existe (ou em qualquer
  falha, via `trap`).
- Off-host envia **apenas o artefato cifrado**.
- Nunca escrevemos conninfo `password=` (minúsculo) — sempre `PGPASSWORD` via env.

## Variáveis de ambiente

### Backup (`backup-native.sh`)

Obrigatórias:

| Var | Descrição |
|---|---|
| `BACKUP_DOCKER_NETWORK` | rede Docker do projeto EasyPanel onde o `postgres-native-90` está |
| `APPSELL_POSTGRES_HOST` | host/alias do Postgres (ex.: `sell-autonomia_postgres-native-90`) |
| `APPSELL_POSTGRES_USER` | role usada no dump (leitura basta) |
| `APPSELL_POSTGRES_SECRET` | senha dessa role (valor só via env) |
| `APPSELL_BACKUP_ENCRYPTION_SECRET` | passphrase simétrica gpg |

Opcionais (defaults):

| Var | Default |
|---|---|
| `APPSELL_POSTGRES_PORT` | `5432` |
| `APPSELL_POSTGRES_DB` | `appsell_native_90` |
| `APPSELL_BACKUP_DIR` | `/var/backups/appsell-native-90` |
| `APPSELL_BACKUP_KEEP` | `7` |
| `APPSELL_BACKUP_DEST_KIND` | `none` \| `local-dir` \| `s3` \| `rsync` |
| `BACKUP_PG_IMAGE` | `postgres:16-alpine` |

### Destino off-host

`s3` (MinIO/S3-compatível, via container `minio/mc`):

| Var | Descrição |
|---|---|
| `APPSELL_BACKUP_S3_ENDPOINT` | ex.: `https://storage.<host>` |
| `APPSELL_BACKUP_S3_BUCKET` | bucket de destino |
| `APPSELL_BACKUP_S3_PREFIX` | prefixo (default `sell/native-90`) |
| `APPSELL_BACKUP_S3_ACCESS_KEY` / `APPSELL_BACKUP_S3_SECRET_KEY` | credenciais (só via env) |

`rsync` (SSH):

| Var | Descrição |
|---|---|
| `APPSELL_BACKUP_RSYNC_TARGET` | `user@host:/caminho` |
| `APPSELL_BACKUP_SSH_KEY_FILE` | caminho da chave privada SSH |
| `APPSELL_BACKUP_SSH_KNOWN_HOSTS` | known_hosts (default `~/.ssh/known_hosts`) |

`local-dir` (usado pelo teste de CI): copia para `APPSELL_BACKUP_LOCAL_DIR`.

### Restore ensaiado (`restore-native.sh`)

```sh
APPSELL_BACKUP_ENCRYPTION_SECRET=... \
  ./restore-native.sh /var/backups/appsell-native-90/appsell_native_90-<stamp>.dump.gpg
```

Sobe um Postgres descartável, decifra, restaura e imprime resumo de verificação.
`RESTORE_KEEP_ALIVE=1` mantém o container vivo para inspeção manual.

## Teste local / CI

```sh
# Requer Docker + gpg
bash deploy/easypanel-native/backup/test-backup-native.sh       # nível biblioteca
bash deploy/easypanel-native/backup/test-entrypoints-native.sh  # entrypoints reais
```

Em PR, mudanças de backup recebem validação sintática rápida no `App CI`. Os
dois ensaios completos rodam no `ci-gate` sobre o SHA integrado do merge group
quando esse escopo muda, e semanalmente no workflow `EasyPanel native images`
(job `backup-restore-rehearsal`). O primeiro exercita as funções da biblioteca;
o segundo executa `backup-native.sh` e `restore-native.sh` como processos reais.
