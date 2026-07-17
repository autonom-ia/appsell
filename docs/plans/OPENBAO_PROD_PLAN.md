# Plano — OpenBao Transit em produção (pré-requisito de login real)

Refs #93 · Parte do P0 Bloco 1 (login real: Identity + vault prod + bootstrap superadmin).

> Documento de planejamento. Nada aqui foi executado. Toda etapa marcada 🔴
> exige aprovação explícita do Rodrigo antes de rodar. Nenhuma escrita em VPS,
> EasyPanel, DNS, banco ou secret foi ou será feita por este slice de docs.

## 0. Por que este plano existe (fatos do código)

Hoje o AppSell **não tem provider de vault utilizável em produção**. Isso é
bloqueio incondicional, verificado no código de `origin/main`:

| Bloqueio | Arquivo | Evidência |
|---|---|---|
| Provider local proibido em prod | `lib/token-vault.ts:183` e `:222` | `throw ... "Local vault key provider is forbidden in production."` (só liberado por `capacityLocalCiRuntimeEnabled()`/`publicDemoRuntimeEnabled()`) |
| Provider OpenBao proibido em prod | `lib/openbao-transit-key-envelope-provider.server.ts:39-41` | `if (NODE_ENV === "production" ...) throw "OpenBao Transit vault provider is not certified for production."` |
| Seletor de provider | `lib/token-vault-provider.server.ts:15-24` | `APPSELL_VAULT_PROVIDER` ∈ {`local`,`openbao-transit`}; qualquer outro → erro |

Consequência: com `NODE_ENV=production`, **os dois** caminhos lançam na
construção do vault. Login real que persista tokens OAuth cifrados é impossível
até (a) subir um OpenBao de produção real e (b) certificar o provider por código.

O que **já existe e é reaproveitável** (slice #19, Local/CI):
- Provider `OpenBaoTransitKeyEnvelopeProvider` com wrap/unwrap AAD-autenticado,
  fail-closed, resposta limitada (16 KiB), `redirect: manual`, `credentials: omit`.
- Contrato de bootstrap Transit exato em `scripts/test-openbao-transit.sh`
  (mount, `disable_upsert`, chave `aes256-gcm96` não exportável, policy runtime,
  token órfão 15 min). **É a fonte de verdade dos comandos** deste plano.
- Policy `infra/openbao/policies/appsell-transit-runtime.hcl` (só `encrypt` e
  `decrypt` da chave exata).
- Imagem fixada por digest: `quay.io/openbao/openbao:2.5.4@sha256:436eaf9778cad75507ff70ea26ace30dcbe15606e619ac3823495663d7f7c115` (`compose.yaml:161`).
- Contrato documentado em `docs/openbao-transit-local-ci.md` (requisitos 74-85 = a
  lista dos bloqueios de produção que este plano tem que fechar).

O que **NÃO existe** e este plano precisa criar: OpenBao com storage durável +
unseal (o compose atual roda `server -dev`, memória volátil), DR do unseal
material, autenticação de workload durável para o BFF, e o gate de código que
libere o provider em prod sob condições estritas.

---

## 1. Decisão de topologia

### Restrições que forçam a decisão (não são preferência)

1. **`server -dev` é proibido em prod.** `compose.yaml:164` usa
   `command: ["server","-dev", ...]` — storage em memória, auto-unseal, root
   token vindo de env. Serve só para teste descartável. Produção exige storage
   persistente (integrated storage / Raft ou `file`) e unseal Shamir real.
2. **O provider só aceita `http://` em loopback.** `parseAddress`
   (`openbao-transit-key-envelope-provider.server.ts:167-179`) aceita `http:`
   **apenas** para `127.0.0.1`/`localhost`/`[::1]`; qualquer outro host exige
   `https:`. No EasyPanel nativo, a comunicação entre App Services usa DNS
   interno de serviço (ex.: `openbao:8200`) — **não é loopback**. Logo, ou o
   OpenBao serve **TLS**, ou o `api`/`web` alcançam o OpenBao por **loopback**
   (sidecar/proxy no mesmo net namespace). Isso decide a topologia.
3. **Token órfão de 15 min não renovável não sobrevive a um serviço long-running.**
   O bootstrap Local/CI cria um token `ttl=15m, renewable=false` (script linha
   59). Um web/api de produção que rode dias não pode depender de um token
   estático que expira em 15 min. Prod precisa de **auth de workload renovável**
   (AppRole com secret-id, ou token periódico), não do token estático do CI.

### Opções avaliadas

| Opção | Descrição | Prós | Contras | Veredito |
|---|---|---|---|---|
| **A — App Service nativo no projeto `sell-autonomia`, storage Raft, sem porta pública, rede interna, TLS interno** | OpenBao como App Service EasyPanel 2.31 nativo, 1 réplica, imagem por digest, volume dedicado para `/openbao/data`, `server` com config file (não `-dev`), listener TLS, alcançado por `api`/`web` via DNS interno `https://openbao-90:8200` | Isolado no projeto certo; sem exposição pública; nativo (padrão do #90); backup do volume via EasyPanel; satisfaz a regra "https para não-loopback" | EasyPanel 2.31 App Service nativo **não** expõe PIDs limit nem rootfs read-only (ver README easypanel-native, linhas 20-21); precisa gerar/gerir cert TLS interno; unseal manual pós-restart | **RECOMENDADA** |
| B — OpenBao App Service + **loopback proxy** no pod do `api` (sem TLS no OpenBao) | Evita TLS: `api` fala `http://127.0.0.1:8200` via proxy que faz bridge pro OpenBao | Dispensa cert | EasyPanel nativo não roda multi-container por App Service de forma trivial; recria a ginástica de proxy do runtime-preview (`runtime-preview-unix-bridge.mjs`); aumenta superfície; OpenBao continua precisando de TLS ou rede confiável | Rejeitada (complexidade > ganho) |
| C — OpenBao via Compose custom (fora do schema nativo) | Reusa `compose.yaml` endurecido (rootfs RO, cap_drop, pids_limit) | Melhor hardening de container | Foge do padrão nativo adotado no #90; EasyPanel gerencia mal serviços Compose ad-hoc; risco operacional e de colisão com Traefik/redes | Rejeitada (desalinha com #90) |
| D — Vault/KMS gerenciado externo (AWS KMS, HCP Vault) | Terceiriza custódia | Zero unseal/DR local | Novo fornecedor, custo, egress, TLS externo, e o provider atual é OpenBao-Transit-específico (AAD, formato `vault:vN:`); exigiria novo provider | Fora de escopo P0; registrar como alternativa futura |

### Recomendação

**Opção A**, com estas travas:
- Imagem **por digest** `quay.io/openbao/openbao:2.5.4@sha256:436eaf97...c115` (mesmo do CI).
- **Sem porta publicada**, sem domínio, sem Traefik. Só rede interna do projeto `sell-autonomia`.
- **Volume dedicado** (ex.: `openbao-90-data`) montado em `/openbao/data`, exclusivo do OpenBao.
- Listener **TLS** interno (cert dedicado); `api`/`web` usam `APPSELL_OPENBAO_ADDR=https://<svc>:8200`.
- **Auth de workload = AppRole** (não token estático de 15 min), com a mesma policy `appsell-transit-runtime`.
- Continua honrando o contrato #19: chave `aes256-gcm96` não exportável, `disable_upsert`, policy só encrypt/decrypt.

> 🔴 A escolha final entre **TLS no OpenBao (A)** vs **loopback proxy (B)** muda o
> código do provider e a config de deploy. Recomendo A. **Decisão do Rodrigo antes de codar.**

---

## 2. Impacto cross-project (VPS multi-tenant 85.31.60.100, EasyPanel 2.31)

Referência de incidente: 2026-05-22 (`destroyService` derrubou Lili + n8n). Regra: nada destrutivo, nada fora do projeto `sell-autonomia`.

### O que este plano TOCA (somente projeto `sell-autonomia`)
- **Cria** 1 App Service novo: `openbao-90` (ou nome equivalente), 1 réplica, imagem por digest.
- **Cria** 1 volume novo dedicado: `openbao-90-data`.
- **Adiciona** env vars nos serviços `api` e `web` nativos do #90 (`APPSELL_VAULT_PROVIDER`, `APPSELL_OPENBAO_*`, credencial AppRole). Toda escrita de env em prod faz **backup antes** (regra 3 do Rodrigo) e nunca omite o campo `env` (lição M11b).
- Consome CPU/RAM/disco do host (ver §3 gate de recursos).

### O que este plano NÃO TOCA (invariante duro)
- **Projetos `flow` e `dados`: INTOCÁVEIS.** Nenhum serviço novo entra nas redes `easypanel-flow` ou `easypanel-dados` (mesma regra do README #90, linha 25).
- Serviços existentes do `sell-autonomia`: `postgres`, `redis`, `storage`, `runtime-preview`, laboratório #71, `postgres-native-90`, `redis-native-90`, `api`, `web` — não são recriados nem destruídos por este slice (exceto o **add** de env vars em `api`/`web`, reversível).
- `sell.autonomia.site` (produção pública) — não é promovido aqui; este plano é o **pré-requisito** de infra, não o cutover de login.
- Nenhum `destroyService`/`deleteService`/`drop*`/`purge*`/`wipe*` em endpoint TRPC EasyPanel. Proibido incondicionalmente.
- DNS, Traefik, billing, IAM: nada muda.

### Riscos cross-project residuais
- **Disco.** appsell#88 reportou raiz ~81%; sem backup hoje. Storage Raft do OpenBao é pequeno (chaves + metadados, MiB), mas o gate de recursos (§3) precisa reconfirmar disco livre antes de qualquer escrita. 🔴
- **Memória/CPU.** OpenBao pede `IPC_LOCK` (mlock) idealmente; App Service nativo pode não conceder cap — avaliar `disable_mlock` (trade-off: chave de unseal pode ir a swap). Registrar como decisão. 🔴

---

## 3. Passo a passo de provisão

> Onde roda: **Console nativo do EasyPanel** dentro do container `openbao-90`
> (shell `bao`/`curl`), salvo indicação. Root token e unseal keys **existem só
> durante o bootstrap** e nunca são entregues ao Next.js (contrato #19).
> **Sequência inteira é 🔴 — nada roda sem OK do Rodrigo.**

### 3.0 Gate pré-escrita (read-only) 🔴 aprovar antes de começar
Inventário read-only antes da 1ª escrita (mesma disciplina do README #90, gates 3-5):
- Listar serviços/containers/redes/CPU/RAM/disco do host.
- Confirmar: memória livre ≥ 8 GiB, disco livre suficiente (rever appsell#88, raiz estava ~81%), load 1min < 6.
- **Abortar** se qualquer limite estourar.

### 3.1 Criar o App Service `openbao-90` 🔴
- Projeto `sell-autonomia`, imagem por digest `...@sha256:436eaf97...c115`.
- **Sem** porta publicada, **sem** domínio.
- Volume `openbao-90-data` → `/openbao/data`.
- Config de servidor **de produção** (NÃO `-dev`): storage `raft` (integrated) em `/openbao/data`, listener TCP com **TLS** habilitado, `api_addr`/`cluster_addr` internos, `ui=false`.
- 1 réplica, `zeroDowntime` desligado no OpenBao (unseal é stateful; não fazer rolling cego).

### 3.2 Init + unseal (Shamir) 🔴 — gera material irrecuperável
No container:
```sh
bao operator init -key-shares=5 -key-threshold=3 -format=json > /tmp/openbao-init.json
```
- Isso emite **5 unseal keys** + **1 root token**. **Material crítico e não regenerável** — ver §4 (DR) ANTES de rodar o primeiro uso real.
- Unseal (repetir com 3 shares distintas):
```sh
bao operator unseal <SHARE_1>
bao operator unseal <SHARE_2>
bao operator unseal <SHARE_3>
```
- Autenticar como root só para o bootstrap:
```sh
export BAO_TOKEN=<ROOT_TOKEN_DO_INIT>
```
> 🔴 As unseal keys e o root token **nunca** vão para chat, log, git, env de serviço. Distribuição/custódia = §4 e decisão do Rodrigo.

### 3.3 Montar o Transit + config (idêntico ao contrato #19)
Espelha `scripts/test-openbao-transit.sh:52-55`:
```sh
bao secrets enable -path=appsell-transit transit
bao write appsell-transit/config/keys disable_upsert=true
bao write appsell-transit/keys/appsell-oauth-vault \
  type=aes256-gcm96 derived=false exportable=false allow_plaintext_backup=false
```
- `exportable=false` + `allow_plaintext_backup=false` ⇒ **a chave nunca sai em plaintext**. Perder o storage+unseal = perder a chave = ciphertext OAuth irrecuperável. É por isso que §4 (DR) é pré-condição.

### 3.4 Policy runtime (só encrypt/decrypt da chave exata)
Reusar `infra/openbao/policies/appsell-transit-runtime.hcl` (2 paths, capability `update`):
```sh
bao policy write appsell-transit-runtime infra/openbao/policies/appsell-transit-runtime.hcl
```
Verificar (deve **falhar**) que a policy não lê metadados da chave:
```sh
# com token runtime: bao read appsell-transit/keys/appsell-oauth-vault  -> deve dar 403
```

### 3.5 Auth de workload durável — **AppRole** (decisão vs token estático)
O CI usa token órfão 15 min (script:59). Para runtime long-running de prod, recomendo AppRole:
```sh
bao auth enable approle
bao write auth/approle/role/appsell-transit-runtime \
  token_policies=appsell-transit-runtime \
  token_ttl=20m token_max_ttl=1h \
  secret_id_ttl=0 token_num_uses=0 \
  token_no_default_policy=true
bao read auth/approle/role/appsell-transit-runtime/role-id
bao write -f auth/approle/role/appsell-transit-runtime/secret-id
```
- `api`/`web` recebem `role-id`+`secret-id` (secrets do serviço), fazem login AppRole e renovam token dentro do processo.
- **Alternativa** (menor mudança de código, pior operação): manter token estático periódico com renovação por um sidecar. Registrado, não recomendado.

> 🔴 **Decisão do Rodrigo:** AppRole (recomendado, exige suporte a login/renovação no provider) vs token estático renovável. Muda o escopo da PR de código (§5).

### 3.6 Selar de novo o root / descartar
- Revogar o root token do bootstrap após configurar (`bao token revoke -self`), operando só via AppRole daí em diante.
- Confirmar `bao status` = unsealed, `sealed=false`.

### 3.7 Fumaça (sem persistir dados reais)
- Com credencial de workload, provar `encrypt` → `decrypt` round-trip de uma DEK de teste retorna a mesma DEK e que a policy nega leitura de metadados. Sem gravar sessão real ainda.

---

## 4. Backup / DR — **pré-condição do primeiro uso real** 🔴

`exportable=false` + `allow_plaintext_backup=false` ⇒ a chave Transit **não pode
ser exportada**. Se o storage do OpenBao e o material de unseal se perderem, todo
ciphertext `vault:vN:*` persistido vira **lixo irreversível** (tokens OAuth
inacessíveis, logins quebrados sem recuperação). Portanto **nada de uso real
antes de o DR abaixo estar feito e testado**.

### 4.1 Custódia do unseal material (feito ANTES do 3.7 / primeiro uso)
- As 5 unseal keys e o root token do `init` → **fora da VPS**, split de custódia (ex.: gerenciador de secrets pessoal do Rodrigo + cópia offline). Nunca no mesmo host, nunca em git, nunca em env de serviço.
- Registrar quem detém cada share e o threshold (3 de 5).
- Considerar **auto-unseal** futuro (transit de um segundo OpenBao ou KMS) para eliminar unseal manual — fora do escopo P0, registrar.

### 4.2 Backup da config + storage
- **Snapshot Raft** do OpenBao (cifrado; só restaurável com o mesmo unseal material):
```sh
bao operator raft snapshot save /openbao/data/snapshots/openbao-<timestamp>.snap
```
  Copiar o snapshot para fora do volume/host (destino sob decisão do Rodrigo). Sem snapshot restaurável, o volume é ponto único de falha.
- Backup **versionado em git** (já é o caso): policy HCL e comandos de bootstrap. A **chave em si nunca** entra em backup plaintext (por design).
- Como não há backup de VPS hoje (fato informado): **exigir** ao menos o snapshot Raft + custódia do unseal antes do primeiro uso. 🔴

### 4.3 Teste de desastre (obrigatório antes de produção real)
- Em ambiente descartável: subir OpenBao limpo, restaurar snapshot, unseal com as shares, e provar que um ciphertext `vault:vN:*` **anterior** ainda descriptografa. Só então o DR está comprovado (fecha item 78-79 de `openbao-transit-local-ci.md`).

---

## 5. Mudança de código no AppSell (a PR de certificação)

Objetivo: liberar o provider OpenBao em `NODE_ENV=production` **somente** sob
condições estritas e auditáveis, sem afrouxar nada para o resto.

### 5.1 O que a PR precisa conter
1. **Gate explícito no provider** (`openbao-transit-key-envelope-provider.server.ts:39-41`):
   - Substituir o `throw` incondicional por um gate que exige uma flag de
     certificação **explícita e default-off**, ex.:
     `APPSELL_OPENBAO_PROD_CERTIFIED=1` **E** endereço `https:` **E**
     credencial de workload presente. Sem a flag, mantém o `throw` (fail-closed).
   - **Sem** `if profile === "X"` espalhado (regra 1 Rodrigo: causa raiz, não gambiarra). Um único ponto de decisão, testável.
2. **TLS / endereço não-loopback**: garantir que `parseAddress` aceite o host
   interno TLS. Ele **já** aceita `https://<host>:<porta>` desde que
   `pathname==="/"`, sem query/hash/creds (linhas 167-179) — validar que o
   endereço interno do EasyPanel passa (host DNS + porta é ok; sem path).
   Se a Opção B (loopback proxy) for escolhida, o gate precisa permitir
   `http://127.0.0.1` em prod — decisão §1.
3. **Auth de workload** (se AppRole, §3.5): implementar login AppRole + renovação
   de token, ou aceitar token periódico via env. Hoje o provider só usa
   `APPSELL_OPENBAO_TOKEN` estático (`fromEnvironment`, linha 68) — insuficiente
   para long-running. Esta é a maior peça nova de código.
4. **Config/flag explícita** e documentada em `.env.example` e nos
   `*.staging.env.example` / env de prod: `APPSELL_VAULT_PROVIDER=openbao-transit`,
   `APPSELL_OPENBAO_ADDR=https://...`, mount/key, timeout, credencial AppRole.
5. **Testes**: reusar `tests/openbao-transit-*.test.ts`; **adicionar** teste que
   prova que sem a flag de certificação o prod continua fechado, e que com
   `https` + flag + credencial abre. Manter os testes de fail-closed
   (indisponibilidade, token inválido, protocolo errado, adulteração).
6. **Docs**: atualizar `docs/openbao-transit-local-ci.md` (ou novo
   `docs/openbao-transit-prod.md`) fechando os itens 74-85; runbook de unseal/DR.
7. **Não tocar** o provider local: `token-vault.ts:183/:222` continua proibindo
   local em prod. O caminho de prod é **exclusivamente** OpenBao certificado.

### 5.2 Critérios de aceite do security-review (hard-assert, zero soft pass)
- [ ] Prod **fechado por default**: sem `APPSELL_OPENBAO_PROD_CERTIFIED`, o provider lança (teste prova).
- [ ] Prod exige `https:` (ou loopback explícito se Opção B) — `http` não-loopback em prod é recusado.
- [ ] DEK, token de workload, unseal key, root token: **nunca** em log, erro, ou corpo de request (testes de vazamento já existem em `openbao-transit-provider.test.ts:92-97` — estender).
- [ ] Policy do workload = só `encrypt`+`decrypt` da chave exata; leitura de metadados dá 403 (prova de integração).
- [ ] Token de workload é renovável/rotacionável (não expira em 15 min matando o serviço).
- [ ] Chave `exportable=false` confirmada; nenhum caminho de código tenta backup plaintext.
- [ ] AAD obrigatória mantida (wrap e unwrap); ciphertext valida versão declarada.
- [ ] Nenhum secret hardcoded; tudo via env/secret manager; `.env.example` sem valores reais.
- [ ] Sem gambiarra `if profile`/try-catch swallow; erro sanitizado e fail-closed.
- [ ] Cross-tenant: nada do gate afeta `flow`/`dados`; provider não lê chave de outro tenant.

Invocar antes do merge: `Appsell-issue-90:security-review` (auth/secrets) e o
reviewer específico + tester de prod (regras 4 e 9 do Rodrigo). CI gate ≠ review.

---

## 6. Rollback

### 6.1 Rollback do código (PR de certificação)
- A flag é **default-off**: reverter é remover `APPSELL_OPENBAO_PROD_CERTIFIED`
  do env dos serviços (backup do env antes; nunca omitir campo `env`) → provider
  volta a lançar em prod, sistema fecha. Reverter o merge da PR restaura o
  `throw` incondicional.

### 6.2 Rollback da infra OpenBao
- Aditivo: `api`/`web` só passam a depender do OpenBao quando o env aponta pra ele. Rollback = repor env anterior (`APPSELL_VAULT_PROVIDER` do estado salvo) e **parar** (não destruir) `openbao-90`.
- **Preservar** volume `openbao-90-data` e snapshots para diagnóstico (mesma disciplina do rollback #90, linhas 94-103: nada de banco/volume apagado).
- Safety-abort (regra 3 Rodrigo): se pós-deploy a contagem de env cair, serviço não ficar healthy, ou breaker abrir → **abortar e restaurar** env do backup sem novo 🟢.

### 6.3 O que rollback NÃO faz
- Não apaga a chave Transit nem o storage (perda seria irreversível).
- Não toca `flow`/`dados`, DNS, Traefik, `sell.autonomia.site`.

### 6.4 Pré-condições documentadas antes de qualquer 🔴 de deploy
- Comando exato de restauração de env + ETA.
- Snapshot Raft salvo e custódia de unseal confirmada (§4).
- Healthz pós-deploy: `bao status` unsealed + round-trip encrypt/decrypt + `/health/ready` do `api`.

---

## 7. Índice de pontos que exigem aprovação do Rodrigo (🔴)

| # | Passo | Por que é 🔴 |
|---|---|---|
| 1 | §1 escolha A (TLS) vs B (loopback proxy) | muda código do provider e deploy |
| 2 | §2 uso de `disable_mlock` / cap `IPC_LOCK` | trade-off de segurança (chave em swap) |
| 3 | §3.0 gate de recursos / disco (appsell#88, sem backup hoje) | pode abortar tudo |
| 4 | §3.1 criar App Service `openbao-90` + volume | 1ª escrita em infra prod |
| 5 | §3.2 init/unseal | gera material irrecuperável |
| 6 | §3.3-3.6 mount/chave/policy/AppRole | config de secret/auth em prod |
| 7 | §3.5 AppRole vs token estático | muda escopo da PR de código |
| 8 | §4 DR (custódia unseal + snapshot + teste desastre) | **pré-condição do 1º uso real** |
| 9 | §5 merge da PR de certificação | toca auth/secrets/vault |
| 10 | Deploy: flip de env em `api`/`web` | escrita de env em prod (backup antes, nunca omitir `env`) |

Nenhum item acima roda sem 🟢 explícito. Este documento é só o plano.

---

## 8. Próxima ação
1. Rodrigo decide §1 (A vs B) e §3.5 (AppRole vs token estático).
2. Abrir Issue de execução filha de #93 para a PR de código de certificação.
3. Só então: gate de recursos read-only (§3.0) e sequência 🔴 sob aprovação.

## Project update pendente
- Projeto: Autonom.ia Dev
- Status: Todo (plano em revisão)
- Tipo: Docs / Infra-plan
- Prioridade: P0 (bloqueia login real)
- Risco: Alto (secrets, auth, prod multi-tenant, DR de material irrecuperável)
- Próxima ação: decisão do Rodrigo em §1 e §3.5
- Ambiente: production (planejamento; nada executado)
