# Runbook — Backup & Restore do Autonom.ia Sell (native-90)

Banco: `appsell_native_90` · Serviço EasyPanel: `postgres-native-90` ·
Projeto: `sell-autonomia` · VPS `85.31.60.100` · Storage off-host: MinIO
(serviço `storage`).

Estado atual (2026-08-24): existem dumps lógicos validados antes dos cutovers
`2c8a3689` e web `1d2ea631`, preservados antes das trocas de imagem.
Ainda não há evidência de rotina recorrente diária nem PITR/WAL concluído. Este
runbook cobre a primeira camada — dump lógico diário, cifrado, off-host, com
restore ensaiado. PITR/WAL está na seção "Evolução" como próximo passo de infra 🔴.

Evidência do dump pré-cutover:
`/home/claudete/backups/appsell/appsell_native_90-before-2c8a3689-20260823-183653.dump`;
SHA-256 `509134d43c1be6f502965b83eecc51eb4f68278ff2a7f0025f7aa5fb93bbea12`.

Evidência adicional do cutover web de 2026-08-24:
`/home/claudete/backups/appsell/pre-web-1d2ea631.dump`; SHA-256
`2a800ceea2605c3cf9b59b7a7d436d03b9c629cacf34365ae1384cbaa31dfd11`;
`pg_restore --list` validado antes do deploy.

Scripts: `deploy/easypanel-native/backup/` (ver `README.md` do diretório para o
contrato completo de variáveis).

Convenção de faixa:
- 🟢 read-only / local / reversível — pode rodar sem novo OK.
- 🔴 escrita em infra/produção/secret — **exige OK explícito do Rodrigo antes**.

---

## 1. Modelo de ameaça e o que este backup cobre

| Cenário | Coberto por dump lógico diário? |
|---|---|
| Drop acidental de tabela / delete em massa | Sim (perda até 24h) |
| Corrupção lógica de dados | Sim (restore para outro banco e cirurgia) |
| Perda do volume / serviço Postgres | Sim, desde que o off-host esteja íntegro |
| Perda do host inteiro (VPS) | Sim **se** o off-host for outra infra/região |
| Recuperação a um ponto no tempo arbitrário (ex.: 14h37) | **Não** — requer PITR/WAL (seção 8) |

**RPO honesto (hoje):** até 24h (janela entre dumps diários). Um delete às 09h
com dump às 03h perde 6h de dados criados após o dump.
**RTO honesto (hoje):** ~15–40 min para restaurar o dump em um banco novo, mais o
tempo de recriar/repontar o serviço. Não há failover automático.

Com PITR (seção 8) o RPO cai para segundos/minutos e o RTO para o tempo de
replay de WAL.

---

## 2. Pré-requisitos no host

- Docker (já presente — é o host EasyPanel).
- `gpg` no host (para cifra/decifra). Confirmar: `gpg --version` 🟢.
- Diretório local de backup com permissão restrita, ex.: `/var/backups/appsell-native-90` (0700).
- Secrets disponíveis como variáveis de ambiente **em runtime**, nunca em arquivo commitado:
  - `APPSELL_POSTGRES_SECRET` — senha da role de leitura do `postgres-native-90`.
  - `APPSELL_BACKUP_ENCRYPTION_SECRET` — passphrase gpg (gerar 32 bytes: `openssl rand -hex 32`).
  - Credenciais do destino off-host (S3/MinIO ou SSH) conforme `APPSELL_BACKUP_DEST_KIND`.

> A passphrase gpg é a chave do reino: sem ela, os backups são inúteis. Guardar
> no mesmo cofre de secrets do projeto (não no host, não no repo). Perder a
> passphrase = perder todos os backups.

Descobrir a rede Docker do projeto EasyPanel (`BACKUP_DOCKER_NETWORK`) 🟢:

```sh
docker network ls --filter name=sell-autonomia
```

Descobrir o alias/host do Postgres nativo 🟢 (normalmente
`sell-autonomia_postgres-native-90`):

```sh
docker ps --filter name=postgres-native-90 --format '{{.Names}}\t{{.Networks}}'
```

---

## 3. Papel de leitura para o backup (uma vez) 🔴

O dump pode usar a role de runtime, mas o correto é uma role dedicada só de
leitura. Criar `appsell_backup` com `pg_read_all_data` **exige escrita no banco de
produção → 🔴 OK do Rodrigo**. Comando previsto (executar via console do
`postgres-native-90`, uma vez):

```sql
-- 🔴 requer OK explícito — escrita em banco de produção
create role appsell_backup login password :'backup_secret';
grant pg_read_all_data to appsell_backup;
```

Enquanto essa role não existir, o backup pode rodar com a role de runtime já
existente (leitura), sem nova escrita no banco.

---

## 4. Backup manual (primeira execução assistida) 🔴

Primeira execução é 🔴 porque conecta ao Postgres de produção e escreve o
artefato cifrado off-host. É **read-only no banco** (só `pg_dump`), mas o envio
off-host é uma escrita externa. Rodar com OK:

```sh
# 🔴 conecta ao Postgres de produção (read-only) e envia off-host cifrado
export BACKUP_DOCKER_NETWORK=<rede-do-projeto>
export APPSELL_POSTGRES_HOST=sell-autonomia_postgres-native-90
export APPSELL_POSTGRES_USER=<role-de-leitura>
export APPSELL_POSTGRES_SECRET=<via-cofre>            # nunca ecoar
export APPSELL_BACKUP_ENCRYPTION_SECRET=<via-cofre>   # nunca ecoar
export APPSELL_BACKUP_DEST_KIND=s3
export APPSELL_BACKUP_S3_ENDPOINT=https://<storage-endpoint>
export APPSELL_BACKUP_S3_BUCKET=<bucket>
export APPSELL_BACKUP_S3_ACCESS_KEY=<via-cofre>
export APPSELL_BACKUP_S3_SECRET_KEY=<via-cofre>

bash deploy/easypanel-native/backup/backup-native.sh
```

Confirmar no log:
- `integrity ok: ... lists N archive entries`
- `encrypted ... -> ....dump.gpg`
- `off-host: uploaded ... to s3://...`
- `backup ok: ....dump.gpg`

Verificar no destino que o objeto `.dump.gpg` chegou com o tamanho esperado 🟢.

---

## 5. Agendamento diário

Duas opções. Escolha uma; ambas exigem que os secrets estejam disponíveis ao
processo sem ficarem em arquivo legível por outros.

### Opção A — cron do host (recomendada para começar)

Arquivo `/etc/cron.d/appsell-native-backup` (permissão 0600), rodando 03:15 UTC:

```cron
# 🔴 criar este arquivo exige acesso ao host — OK do Rodrigo
15 3 * * * root /usr/bin/env - \
  BACKUP_DOCKER_NETWORK=<rede> \
  APPSELL_POSTGRES_HOST=sell-autonomia_postgres-native-90 \
  APPSELL_POSTGRES_USER=<role> \
  APPSELL_BACKUP_DEST_KIND=s3 \
  APPSELL_BACKUP_S3_ENDPOINT=https://<endpoint> \
  APPSELL_BACKUP_S3_BUCKET=<bucket> \
  bash -c 'set -a; . /root/.config/appsell-backup.env; set +a; \
    /opt/appsell/deploy/easypanel-native/backup/backup-native.sh \
    >> /var/log/appsell-backup.log 2>&1'
```

Os secrets (`APPSELL_POSTGRES_SECRET`, `APPSELL_BACKUP_ENCRYPTION_SECRET`,
`APPSELL_BACKUP_S3_*`) ficam em `/root/.config/appsell-backup.env` (0600), fora do
repo. Rotação e valores 🔴.

### Opção B — serviço agendado no EasyPanel

Criar um App Service pequeno (ex.: `backup-native-90`) a partir do mesmo repo/SHA,
com os scripts, secrets como env do serviço e um schedule/cron do EasyPanel
chamando `backup-native.sh`. Vantagem: secrets no cofre do EasyPanel, sem tocar o
host. Requer que o serviço tenha acesso ao socket Docker **ou** que o dump use
conexão de rede direta (sem `docker run`) — avaliar como evolução; a Opção A não
depende disso.

> Enquanto o agendamento não for aprovado, rodar o backup manual (seção 4)
> diariamente já elimina o risco de "zero backup".

---

## 6. Restore ensaiado (teste mensal) 🟢

Ensaiar restore é **read-only sobre produção** (só baixa o `.dump.gpg` do
off-host) e cria um Postgres **descartável** local. Não toca o banco de produção.
Rodar todo mês:

```sh
# baixar o backup mais recente do off-host para um arquivo local (🟢)
#   (via mc/aws para S3, ou já presente em APPSELL_BACKUP_DIR)
export APPSELL_BACKUP_ENCRYPTION_SECRET=<via-cofre>
bash deploy/easypanel-native/backup/restore-native.sh \
  /caminho/para/appsell_native_90-<stamp>.dump.gpg
```

Aceite do ensaio:
- `integrity ok: ...`
- `restored non-system tables: N` com `N > 0`
- `identity_user rows restored: M` (quando aplicável)
- `restore rehearsal ok: ... is restorable`

Registrar data + resultado do ensaio (ver seção 9). Um backup que nunca foi
restaurado não é um backup.

---

## 7. Restore real de emergência (recuperação de desastre) 🔴

**Todo restore que substitui dados de produção é 🔴 e exige OK explícito +
backup do estado atual antes.**

Sequência prevista:

1. 🔴 **Backup do estado atual primeiro** (mesmo corrompido): rodar `backup-native.sh`
   ou snapshot do volume — nunca restaurar por cima sem rede de segurança.
2. Baixar o `.dump.gpg` alvo do off-host e **decifrar + verificar** com
   `restore-native.sh` (ensaio em descartável) para confirmar que é restaurável.
3. Decidir estratégia:
   - **Novo banco** `appsell_native_90_restored` e repontar a API (mais seguro,
     rollback trivial), ou
   - Restore por cima do banco existente (`pg_restore --clean --if-exists`) —
     destrutivo, só com OK e após o passo 1.
4. Executar o `pg_restore` contra o `postgres-native-90` 🔴 (via console/rede do
   serviço, com `PGPASSWORD` da role admin por env).
5. Validar: `/health/ready` da API, contagem de `identity_user`, fluxo de login
   demo. Só então cortar tráfego para o banco restaurado.
6. Se algo falhar, **rollback**: repontar para o banco original preservado no
   passo 1.

RTO alvo desta sequência: ~15–40 min (dump pequeno de demo). Documentar no
incidente cada passo executado.

---

## 8. Evolução — PITR / WAL (próximo passo de infra 🔴, não implementado)

Dump lógico diário = RPO de até 24h. Para RPO de segundos/minutos e recuperação
a um ponto arbitrário no tempo, é preciso **PITR baseado em WAL**. Isso **não está
implementado** aqui de propósito, porque exige acesso ao volume/config do
Postgres nativo — decisão de infra do Rodrigo.

Plano quando aprovado:

1. **Base backup + arquivamento de WAL.** Habilitar `archive_mode=on` e
   `archive_command` no `postgres-native-90` enviando segmentos de WAL para o
   off-host (MinIO). Requer editar config do Postgres e acesso ao volume 🔴.
2. **Ferramenta dedicada.** Avaliar `pgBackRest` ou `wal-g` (ambos suportam
   S3/MinIO e cifra nativa) rodando como sidecar/serviço com acesso ao
   `PGDATA`. `pgBackRest` dá full/incremental + retenção + verificação; `wal-g`
   é mais leve e S3-first.
3. **Restore PITR.** Base backup + replay de WAL até
   `recovery_target_time='YYYY-MM-DD HH:MM:SSZ'` em um Postgres de recuperação.
4. **Ensaovar PITR mensalmente**, como já se faz com o dump lógico.

Bloqueios/decisões para o Rodrigo:
- Acesso ao volume/`PGDATA` do serviço nativo (o schema atual do App Service
  EasyPanel não expõe mounts — pode exigir um serviço Postgres com volume
  dedicado ou mudança de topologia).
- Custo/retenção de WAL no MinIO.
- Chave de cifra do WAL (mesma disciplina de `APPSELL_BACKUP_ENCRYPTION_SECRET`).

Até essa decisão, o dump lógico diário cifrado off-host é a proteção vigente e já
remove o estado "zero backup".

---

## 9. Registro de ensaios e checklist operacional

Manter uma linha por ensaio/restore (neste runbook ou em `docs/audit/`):

| Data | Ação | Artefato | Resultado | Operador |
|---|---|---|---|---|
| _(preencher)_ | ensaio mensal | `appsell_native_90-<stamp>.dump.gpg` | ok / falha | |

Checklist mensal:
- [ ] Backup diário rodando (últimos 7 `.dump.gpg` presentes local + off-host).
- [ ] Rotação mantendo 7 diários (sem acúmulo infinito).
- [ ] Ensaio de restore (seção 6) verde no mês.
- [ ] Passphrase gpg e credenciais off-host ainda válidas no cofre.
- [ ] Off-host em infra/região distinta da VPS (senão perda do host derruba tudo).
