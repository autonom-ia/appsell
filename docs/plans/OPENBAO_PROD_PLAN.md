# Plano — OpenBao Transit em produção (pré-requisito de login real)

Refs #93 · Parte do P0 Bloco 1 (login real: Identity + vault prod + bootstrap superadmin).

> Documento de planejamento. Nada aqui foi executado. Toda etapa marcada 🔴
> exige aprovação explícita do Rodrigo antes de rodar. Nenhuma escrita em VPS,
> EasyPanel, DNS, banco ou secret foi ou será feita por este slice de docs.

## Rev 2 — resolução do security-review (PR #100)

O review externo (codex `gpt-5.6-sol`, read-only sobre `git diff origin/main...HEAD`)
**REPROVOU** a Rev 1 com 7 HIGH. Esta revisão fecha os 7, aplicando decisões já
tomadas pelo Rodrigo (régua qualidade + escalabilidade + segurança). Mapa:

| # | HIGH original | Decisão aplicada | Seção |
|---|---|---|---|
| 1 | Topologia single-node contradiz requisito de HA | **NÃO alegar HA.** Single-node Raft é MVP explícito, não-HA, com RTO/RPO declarados e risco aceito por escrito; DR (snapshot + unseal) é pré-condição do 1º uso | §1, §1.1, §4 |
| 2 | Bootstrap grava unseal keys + root token em plaintext no host | **Sem plaintext no host.** `init` com `-pgp-keys`/`-root-token-pgp-key` (shares/root cifrados aos donos) **ou** auto-unseal por KMS; root token revogado após setup; unseal keys guardadas fora da VPS (cofre do Rodrigo). Zero `/tmp/*.json` em claro | §3.2, §4.1 |
| 3 | AppRole é credencial estática de uso ilimitado | **SecretID com TTL curto + `num_uses` limitado + rotação** (response-wrapping). Token de runtime órfão, não-renovável-além-do-TTL, TTL 15min, policy única `appsell-transit-runtime` (só encrypt/decrypt da chave exata). Sem `secret_id_ttl=0` / usos ilimitados | §1.2, §3.5 |
| 4 | Credencial de vault iria para `api` **e** `web` | **Credencial Transit só no `web`/BFF** (único consumidor de `createTokenVaultFromEnvironment`). `api` não recebe nada de vault | §2, §3.5, §5 |
| 5 | Bootstrap de policy não é executável (path do `.hcl` não existe no container) | **Policy via API `sys/policies/acl/...`** com HCL inline (idêntico ao CI) ou `bao policy write NAME -` via stdin. Config do server + TLS cert/key entregues por mount dedicado, não pelo repo | §3.1, §3.4 |
| 6 | TLS interno sem CA confiável no runtime | **CA privada explícita:** cert do listener emitido por CA privada da VPS, com SAN = DNS interno do serviço; CA bundle montado no container `web` e exposto ao Node via `NODE_EXTRA_CA_CERTS`. Verificação TLS **nunca** desabilitada | §1.1, §3.1, §5.1 |
| 7 | "Rollback" não é rollback depois do 1º ciphertext | **Rollback honesto:** distinção pré-1º-write (seguro) vs pós-persistência (fail-closed, **não** recupera envelopes). Recuperação real = restaurar snapshot + unseal. Provider local só em não-produção | §6 |

MEDIUM tratados: observabilidade/hardening (audit device, limites CPU/RAM, alertas
de sealed/TLS/AppRole) em §3.8; escopo honesto de quais itens de
`openbao-transit-local-ci.md:74-85` este plano fecha vs deixa aberto em §4.4.
LOW corrigido: `compose.yaml:163` (era citado `:164`).

---

## 0. Por que este plano existe (fatos do código)

Hoje o AppSell **não tem provider de vault utilizável em produção**. Isso é
bloqueio incondicional, verificado no código de `origin/main`:

| Bloqueio | Arquivo | Evidência |
|---|---|---|
| Provider local proibido em prod | `lib/token-vault.ts:183` e `:222` | `throw ... "Local vault key provider is forbidden in production."` |
| Provider OpenBao proibido em prod | `lib/openbao-transit-key-envelope-provider.server.ts:39-41` | `if (NODE_ENV === "production" ...) throw "OpenBao Transit vault provider is not certified for production."` |
| Seletor de provider | `lib/token-vault-provider.server.ts:15-24` | `APPSELL_VAULT_PROVIDER` ∈ {`local`,`openbao-transit`}; qualquer outro → erro |

Consequência **precisa**: com `NODE_ENV=production` e **sem** as flags de exceção
`capacityLocalCiRuntimeEnabled()` / `publicDemoRuntimeEnabled()` (`token-vault.ts:179-183`/`:218-222`),
os dois caminhos lançam na construção do vault. Essas duas flags são as únicas
exceções (CI de capacidade e demo público) e **não** valem para prod real. Login
real que persista tokens OAuth cifrados é impossível até (a) subir um OpenBao de
produção real e (b) certificar o provider por código.

O que **já existe e é reaproveitável** (slice #19, Local/CI):
- Provider `OpenBaoTransitKeyEnvelopeProvider` com wrap/unwrap AAD-autenticado,
  fail-closed, resposta limitada (16 KiB), `redirect: manual`, `credentials: omit`.
- Contrato de bootstrap Transit exato em `scripts/test-openbao-transit.sh` — que
  faz **tudo via API HTTP** (`curl` para `sys/mounts/...`, `sys/policies/acl/...`,
  `auth/token/create-orphan`), não via arquivos locais dentro do container. **É a
  fonte de verdade dos comandos** deste plano.
- Policy `infra/openbao/policies/appsell-transit-runtime.hcl` (só `update` em
  `encrypt/appsell-oauth-vault` e `decrypt/appsell-oauth-vault`).
- Imagem fixada por digest: `quay.io/openbao/openbao:2.5.4@sha256:436eaf9778cad75507ff70ea26ace30dcbe15606e619ac3823495663d7f7c115` (`compose.yaml:161`).
- Consumidor único: `createTokenVaultFromEnvironment` é importado **só** por
  `lib/oauth-service.server.ts` (BFF Next.js = serviço `web`). Nenhum arquivo
  Python em `apps/api` referencia vault/openbao.

O que **NÃO existe** e este plano precisa criar: OpenBao com storage durável +
unseal (o `compose.yaml:163` roda `server -dev`, memória volátil), DR do unseal
material, autenticação de workload durável para o BFF, e o gate de código que
libere o provider em prod sob condições estritas.

---

## 1. Decisão de topologia

### Restrições que forçam a decisão (não são preferência)

1. **`server -dev` é proibido em prod.** `compose.yaml:163` usa
   `command: ["server","-dev","-dev-listen-address=0.0.0.0:8200"]` — storage em
   memória, auto-unseal, root token vindo de env. Serve só para teste
   descartável. Produção exige storage persistente (integrated storage / Raft) e
   unseal Shamir real.
2. **O provider só aceita `http://` em loopback.** `parseAddress`
   (`openbao-transit-key-envelope-provider.server.ts:160-179`) aceita `http:`
   **apenas** para `127.0.0.1`/`localhost`/`[::1]`; qualquer outro host exige
   `https:`. No EasyPanel nativo, a comunicação entre App Services usa DNS interno
   de serviço (ex.: `openbao-90:8200`) — **não é loopback**. Logo, o OpenBao
   precisa servir **TLS** (Opção A). O provider usa `fetch()` puro, sem nenhum
   tratamento de CA custom, então TLS interno exige CA confiável explícita
   (§1.1) — não basta a URL ser `https:`.
3. **Token órfão de 15 min não renovável não sobrevive sozinho a serviço long-running.**
   O bootstrap Local/CI cria token via `auth/token/create-orphan`
   (`ttl:15m, renewable:false, no_parent:true, no_default_policy:true`, script
   linhas 59-60). Manteremos exatamente essas propriedades de token em prod
   (decisão do Rodrigo), mas o serviço **re-autentica via AppRole** para obter um
   token novo de 15 min antes de expirar, em vez de renovar/estender — ver §1.2.

### Opções avaliadas

| Opção | Descrição | Prós | Contras | Veredito |
|---|---|---|---|---|
| **A — App Service nativo em `sell-autonomia`, storage Raft single-node, sem porta pública, rede interna, TLS interno com CA privada** | OpenBao App Service EasyPanel 2.31 nativo, 1 réplica, imagem por digest, volume dedicado `/openbao/data`, `server -config=` (não `-dev`), listener TLS, alcançado por `web` via DNS interno `https://openbao-90:8200` | Isolado no projeto certo; sem exposição pública; nativo (padrão do #90); backup do volume via EasyPanel; satisfaz "https para não-loopback"; superfície mínima | Single-node = **não-HA** (aceito, §1.1); precisa emitir/gerir cert TLS + CA interna; unseal assistido pós-restart; App Service nativo não expõe `pids_limit`/rootfs RO como o compose de CI (README easypanel-native:20-21) | **RECOMENDADA** |
| B — OpenBao App Service + **loopback proxy** no pod (sem TLS) | `web` fala `http://127.0.0.1:8200` via proxy que faz bridge | Dispensa cert | EasyPanel nativo não roda multi-container por App Service trivialmente; recria a ginástica de proxy do runtime-preview; aumenta superfície; OpenBao ainda precisa de rede confiável | Rejeitada (complexidade > ganho) |
| C — OpenBao via Compose custom (fora do schema nativo) | Reusa `compose.yaml` endurecido (rootfs RO, cap_drop, pids_limit) | Melhor hardening de container | Foge do padrão nativo do #90; EasyPanel gerencia mal Compose ad-hoc; risco de colisão com Traefik/redes | Rejeitada (desalinha com #90) |
| D — Vault/KMS gerenciado externo (AWS KMS, HCP Vault) | Terceiriza custódia | Zero unseal/DR local | Novo fornecedor, custo, egress; provider atual é OpenBao-Transit-específico (AAD, formato `vault:vN:`) → exigiria novo provider | Fora de escopo P0; registrar como alternativa futura e possível fonte de auto-unseal (§4.1) |

### Recomendação — **Opção A**, com estas travas
- Imagem **por digest** `...@sha256:436eaf97...c115` (mesmo do CI).
- **Sem porta publicada**, sem domínio, sem Traefik. Só rede interna de `sell-autonomia`.
- **Volume dedicado** `openbao-90-data` → `/openbao/data`, exclusivo do OpenBao.
- Listener **TLS** interno com **CA privada** (§1.1); `web` usa `APPSELL_OPENBAO_ADDR=https://openbao-90:8200`.
- **`mlock` mantido** (cap `IPC_LOCK`, como `compose.yaml:173`). `disable_mlock=true`
  só como **fallback documentado** se o App Service nativo do EasyPanel não conceder
  a cap — trade-off registrado (chave de unseal poderia ir a swap). Decisão 🔴.
- **Auth de workload = AppRole** com SecretID de vida curta + rotação (§1.2), policy única `appsell-transit-runtime`.
- Honra o contrato #19: chave `aes256-gcm96` não exportável, `disable_upsert`, policy só encrypt/decrypt.

### 1.1 Topologia A é **single-node, não-HA** — declaração explícita (resolve HIGH 1)

Este MVP **não é HA** e não será apresentado como tal. Decisão consciente do
Rodrigo para desbloquear o login real (P0) sem introduzir a complexidade de um
cluster Raft 3+ nós agora.

- **Modelo:** 1 nó OpenBao, integrated storage (Raft) local, 1 réplica. Não há quorum, não há failover automático.
- **SPOF assumido:** se o nó/volume cair, o login/OAuth que depende do decrypt fica indisponível até restaurar (unseal manual assistido ou restore de snapshot).
- **RTO alvo (indisponibilidade):** restart + unseal assistido em minutos; restore de snapshot em ambiente novo dentro da janela de manutenção. **RPO:** limitado ao último snapshot Raft (§4).
- **Risco aceito por escrito:** a indisponibilidade temporária do vault é tolerada porque o AppSell ainda não tem tráfego de produção de login real; HA (3+ nós ou auto-unseal por KMS) entra como **evolução pós-MVP registrada**, não como pré-requisito deste slice.
- **Contradição da Rev 1 removida:** este plano **não** afirma resolver "cluster HA/Raft"; ele fecha explicitamente esse item de `openbao-transit-local-ci.md` como *decisão de operar não-HA no MVP* (ver §4.4), não como HA entregue.

**TLS interno com CA confiável (resolve HIGH 6):**
- O listener TLS do OpenBao usa um **cert emitido por uma CA privada da VPS**
  (CA dedicada a serviços internos de `sell-autonomia`), com **SAN = nome DNS
  interno do serviço** (`openbao-90`). Self-signed sem CA não é aceito.
- O container `web` recebe o **CA bundle** dessa CA privada em um path montado
  (ex.: `/etc/appsell/openbao-ca.pem`) e o expõe ao Node via
  `NODE_EXTRA_CA_CERTS=/etc/appsell/openbao-ca.pem`, para que o `fetch()` do
  provider valide o cert interno sem código custom de CA e **sem** desabilitar
  verificação (proibido pela régua de segurança).
- Cert, key e CA são entregues por **mount dedicado** (config/secret do App
  Service), nunca commitados no repo. Expiração do cert entra nos alertas (§3.8).

### 1.2 Auth de workload durável **sem credencial estática ilimitada** (resolve HIGH 3)

Objetivo: nenhum bearer token permanente; token de runtime curto e órfão; sem
`secret_id_ttl=0`/usos ilimitados.

- **AppRole** é o método de login durável. O **SecretID** tem:
  - **`secret_id_ttl` curto e finito** (janela de rotação, ex.: horas — **nunca `0`**);
  - **`secret_id_num_uses` limitado** (não ilimitado);
  - **entrega por response-wrapping** (`wrap_ttl`) para o `web`, de modo que o SecretID em claro nunca fique parado em env/log.
- **Rotação de SecretID** é requisito, não opcional: um mecanismo confiável reemite
  SecretID (wrapped) dentro da janela de TTL. O desenho exato do rotador
  (sidecar/agent vs job) é **decisão 🔴** de execução — mas *não existe caminho em
  que o SecretID vire credencial permanente*.
- **Token de runtime** (resultado do login AppRole): **órfão**, **TTL 15min**,
  `token_max_ttl=15m` (não se estende além de 15 min → efetivamente não-renovável
  em duração), `token_no_default_policy=true`, **policy única
  `appsell-transit-runtime`** (só `encrypt`/`decrypt` da chave exata). O serviço
  **re-loga via AppRole** para obter um token novo antes do vencimento — não renova
  o token velho. Isso concilia "token curto órfão" com serviço long-running.
- **Tensão honesta registrada:** SecretID curto + serviço que precisa re-logar
  exige que a rotação de SecretID esteja de pé **antes** do cutover. Se a rotação
  não estiver pronta, o serviço perde acesso ao expirar o SecretID → fail-closed
  (login indisponível), nunca acesso permanente. A rotação é, portanto,
  pré-condição do deploy (§6.4), não um "nice to have".

> 🔴 **Decisão de execução:** desenho do rotador de SecretID (sidecar vs job vs
> auto-unseal-broker) e janela de `secret_id_ttl`/`num_uses`. Recomendado AppRole
> wrapped + rotação. Muda o escopo da PR de código (§5).

---

## 2. Impacto cross-project (VPS multi-tenant 85.31.60.100, EasyPanel 2.31)

Referência de incidente: 2026-05-22 (`destroyService` derrubou Lili + n8n). Regra: nada destrutivo, nada fora do projeto `sell-autonomia`.

### O que este plano TOCA (somente projeto `sell-autonomia`)
- **Cria** 1 App Service novo: `openbao-90` (ou nome equivalente), 1 réplica, imagem por digest.
- **Cria** 1 volume novo dedicado: `openbao-90-data`.
- **Adiciona** env vars **somente no serviço `web`** nativo do #90
  (`APPSELL_VAULT_PROVIDER`, `APPSELL_OPENBAO_*`, RoleID + entrega wrapped de
  SecretID, `NODE_EXTRA_CA_CERTS`). **O serviço `api` NÃO é tocado** — não consome
  vault (resolve HIGH 4, menor privilégio). Toda escrita de env em prod faz
  **backup antes** (regra 3 do Rodrigo) e nunca omite o campo `env` (lição M11b).
- Consome CPU/RAM/disco do host (ver §3.0 gate de recursos).

### O que este plano NÃO TOCA (invariante duro)
- **Projetos `flow` e `dados`: INTOCÁVEIS.** Nenhum serviço novo entra nas redes `easypanel-flow` ou `easypanel-dados` (README #90:25).
- Serviços existentes do `sell-autonomia` (`postgres`, `redis`, `storage`, `runtime-preview`, lab #71, `postgres-native-90`, `redis-native-90`, `api`, `web`) não são recriados nem destruídos por este slice — exceto o **add** de env vars **em `web`** (reversível). `api` fica intocado.
- `sell.autonomia.site` (produção pública) — não é promovido aqui; este plano é o **pré-requisito** de infra, não o cutover de login.
- Nenhum `destroyService`/`deleteService`/`drop*`/`purge*`/`wipe*` em endpoint TRPC EasyPanel. Proibido incondicionalmente.
- DNS, Traefik, billing, IAM: nada muda.

### Riscos cross-project residuais
- **Disco (gate 🔴 bloqueante).** appsell#88 reportou raiz **~81% e sem backup**.
  Storage Raft do OpenBao é pequeno (MiB), mas o gate de recursos (§3.0) **DEVE**
  reconfirmar disco livre **e** a ausência de backup antes de qualquer escrita.
  **Enquanto disco ~81% + sem backup não forem resolvidos, o provisionamento não
  começa** (§3.0). Ver decisão 9.
- **Memória/CPU.** OpenBao pede `IPC_LOCK` (mlock). Manteremos a cap (§1). Se o App
  Service nativo não conceder, `disable_mlock=true` é fallback documentado com
  trade-off registrado. 🔴

---

## 3. Passo a passo de provisão

> Onde roda: **Console nativo do EasyPanel** dentro do container `openbao-90`
> (CLI `bao` — confirmado no PATH pelo healthcheck `compose.yaml:182` — e `curl`
> para a API HTTP), salvo indicação. Root token e unseal keys **existem só durante
> o bootstrap**, cifrados a chaves PGP dos donos (§3.2), e **nunca** são entregues
> ao Next.js nem gravados em claro no host. **Sequência inteira é 🔴 — nada roda
> sem OK do Rodrigo.**

### 3.0 Gate pré-escrita (read-only) 🔴 — pode abortar tudo
Inventário read-only antes da 1ª escrita (mesma disciplina do README #90, gates 3-5):
- Listar serviços/containers/redes/CPU/RAM/disco do host.
- Confirmar: memória livre ≥ 8 GiB, **disco** com folga real (appsell#88: raiz estava ~81%), load 1min < 6.
- **Gate bloqueante (decisão 9):** disco ~81% **e** ausência de backup (appsell#88)
  **devem estar resolvidos** (liberar espaço + backup mínimo da VPS de pé) **antes**
  de provisionar. Sem isso, **ABORTAR** — não criar o App Service.

### 3.1 Criar o App Service `openbao-90` 🔴
- Projeto `sell-autonomia`, imagem por digest `...@sha256:436eaf97...c115`.
- **Sem** porta publicada, **sem** domínio.
- Volume `openbao-90-data` → `/openbao/data`.
- **Config de servidor de produção** entregue por **mount dedicado** (config/secret
  do App Service), não pelo repo: arquivo `config.hcl` com `storage "raft"` em
  `/openbao/data`, `listener "tcp"` com **TLS habilitado**
  (`tls_cert_file`/`tls_key_file`/`tls_client_ca_file` apontando para os arquivos
  montados em §1.1), `api_addr`/`cluster_addr` internos (`https://openbao-90:8200`),
  `ui=false`. Comando do container: `server -config=/etc/openbao/config.hcl` (NÃO `-dev`).
- **`disable_mlock`**: ausente por padrão (mlock ativo via cap `IPC_LOCK`);
  só setar `true` se a cap não for concedida (§2, decisão 🔴).
- 1 réplica; **não** usar rolling/`zeroDowntime` (unseal é stateful — não fazer rolling cego).

### 3.2 Init + unseal (Shamir) 🔴 — material irrecuperável, **sem plaintext no host** (resolve HIGH 2)
No container, **cifrando shares e root token a chaves PGP dos custodiantes** — o
output nunca contém segredo em claro:
```sh
bao operator init \
  -key-shares=5 -key-threshold=3 \
  -pgp-keys="rodrigo.asc,custodiante2.asc,custodiante3.asc,custodiante4.asc,custodiante5.asc" \
  -root-token-pgp-key="rodrigo.asc" \
  -format=json
```
- Cada unseal key sai **cifrada** à PGP pública do respectivo custodiante; o root
  token sai cifrado à PGP do Rodrigo. Só o dono da chave privada decifra. **Não há
  arquivo `/tmp/*.json` em claro** e nada é ecoado em chat/log/git/env.
- Distribuição/custódia das públicas e dos shares cifrados = §4.1.
- **Alternativa (recomendada para eliminar unseal manual): auto-unseal** por KMS
  externo ou por um segundo OpenBao (transit). Nesse caso não há Shamir shares para
  custodiar, mas há a dependência do KMS — decisão 🔴 (§4.1).
- Unseal assistido (se Shamir), com 3 shares distintas decifradas pelos donos:
```sh
bao operator unseal   # repetir 3x, cada custodiante insere seu share decifrado
```
- Autenticar como root **só** para o bootstrap (root token decifrado pelo Rodrigo,
  em sessão efêmera, nunca persistido no host):
```sh
export BAO_TOKEN=<ROOT_TOKEN_DECIFRADO_EM_MEMORIA>
```
> 🔴 Unseal keys e root token **nunca** vão para chat, log, git, env de serviço nem
> arquivo em claro no host. Root token é **revogado após o setup** (§3.6).

### 3.3 Montar o Transit + config (idêntico ao contrato #19)
Espelha `scripts/test-openbao-transit.sh:52-55` (via CLI ou a mesma API):
```sh
bao secrets enable -path=appsell-transit transit
bao write appsell-transit/config/keys disable_upsert=true
bao write appsell-transit/keys/appsell-oauth-vault \
  type=aes256-gcm96 derived=false exportable=false allow_plaintext_backup=false
```
- `exportable=false` + `allow_plaintext_backup=false` ⇒ **a chave nunca sai em plaintext**. Perder storage+unseal = perder a chave = ciphertext OAuth irrecuperável. Por isso §4 (DR) é pré-condição.

### 3.4 Policy runtime — **executável sem o `.hcl` do repo** (resolve HIGH 5)
O container é a imagem oficial com volume só em `/openbao/data`; o arquivo
`infra/openbao/policies/appsell-transit-runtime.hcl` **não existe** dentro dele.
Portanto **não** usar `bao policy write NAME <path-do-repo>`. Duas formas válidas:

**(a) Via API HTTP — idêntico ao CI (`test-openbao-transit.sh:57-58`), fonte de verdade:**
```sh
curl --fail-with-body --silent --show-error \
  --header "content-type: application/json" \
  --header "x-vault-token: ${BAO_TOKEN}" \
  --request PUT \
  --data '{"policy":"path \"appsell-transit/encrypt/appsell-oauth-vault\" {\n  capabilities = [\"update\"]\n}\npath \"appsell-transit/decrypt/appsell-oauth-vault\" {\n  capabilities = [\"update\"]\n}\n"}' \
  "https://openbao-90:8200/v1/sys/policies/acl/appsell-transit-runtime"
```
**(b) Via CLI com HCL por stdin (sem arquivo local):**
```sh
bao policy write appsell-transit-runtime - <<'HCL'
path "appsell-transit/encrypt/appsell-oauth-vault" {
  capabilities = ["update"]
}
path "appsell-transit/decrypt/appsell-oauth-vault" {
  capabilities = ["update"]
}
HCL
```
Verificar (deve **falhar** com 403) que a policy não lê metadados da chave:
```sh
# com token runtime: bao read appsell-transit/keys/appsell-oauth-vault  -> 403 esperado
```

### 3.5 Auth de workload — **AppRole com SecretID curto + rotação** (resolve HIGH 3 e 4)
Habilitar AppRole e criar a role com token curto/órfão e SecretID **finito**:
```sh
bao auth enable approle
bao write auth/approle/role/appsell-transit-runtime \
  token_policies=appsell-transit-runtime \
  token_ttl=15m token_max_ttl=15m \
  token_no_default_policy=true \
  secret_id_ttl=2h secret_id_num_uses=1 \
  secret_id_bound_cidrs="<CIDR interno do web>"
bao read auth/approle/role/appsell-transit-runtime/role-id
# SecretID sempre entregue wrapped (nunca em claro em env/log):
bao write -wrap-ttl=120s -f auth/approle/role/appsell-transit-runtime/secret-id
```
- **Só o `web`/BFF** recebe RoleID + o token wrapped do SecretID; faz unwrap, login
  AppRole, e re-loga antes de expirar. **`api` não recebe nada.**
- `secret_id_ttl=2h` + `secret_id_num_uses=1` são valores iniciais — **nunca `0`**;
  ajuste fino na decisão 🔴 do rotador (§1.2). Um rotador reemite SecretID wrapped
  dentro da janela.
- Token de runtime resultante: órfão, `ttl=15m`, `max_ttl=15m` (não estende além de
  15 min), sem default policy, só `appsell-transit-runtime`.

> 🔴 **Decisão do Rodrigo:** desenho e janela do rotador de SecretID (§1.2). Muda o escopo da PR de código (§5).

### 3.6 Revogar root / confirmar estado
- **Revogar** o root token do bootstrap após configurar (`bao token revoke -self`),
  operando só via AppRole daí em diante. Root token não sobrevive ao setup.
- Confirmar `bao status` = unsealed, `sealed=false`.

### 3.7 Fumaça (sem persistir dados reais)
- Com credencial de workload (AppRole), provar `encrypt` → `decrypt` round-trip de
  uma DEK de teste retorna a mesma DEK e que a policy nega leitura de metadados
  (403). Sem gravar sessão real ainda.

### 3.8 Observabilidade / hardening operacional (resolve MEDIUM)
- **Limites de recurso** do App Service `openbao-90`: CPU/RAM definidos
  explicitamente (referência do CI: `mem_limit: 256m`, `cpus: 0.5` em
  `compose.yaml:176-177`; dimensionar para prod).
- **Audit device** habilitado (`bao audit enable file`) gravando em volume
  dedicado, com sanitização — **nunca** logar plaintext de DEK/token.
- **Alertas:** estado `sealed=true` inesperado, expiração próxima do cert TLS,
  falha de login AppRole, falha de rotação de SecretID, uso anômalo (taxa de
  decrypt), disco do volume Raft.
- Health check real: `bao status` (unsealed) + round-trip encrypt/decrypt, não só ping.
- **Aberto e registrado:** staging isolado com observabilidade e SLO
  (`openbao-transit-local-ci.md:81`) **não** é entregue por este slice — ver §4.4.

---

## 4. Backup / DR — **pré-condição do primeiro uso real** 🔴

`exportable=false` + `allow_plaintext_backup=false` ⇒ a chave Transit **não pode
ser exportada**. Se o storage do OpenBao e o material de unseal se perderem, todo
ciphertext `vault:vN:*` persistido vira **lixo irreversível** (tokens OAuth
inacessíveis, logins quebrados sem recuperação). Portanto **nada de uso real antes
de o DR abaixo estar feito e testado**.

### 4.1 Custódia do unseal material (feito ANTES do 3.7 / primeiro uso)
- Shares (cifrados por PGP, §3.2) e root token cifrado → **fora da VPS**, split de
  custódia (cofre de secrets pessoal do Rodrigo + cópia offline). Nunca no mesmo
  host, nunca em git, nunca em env de serviço, nunca em claro.
- Registrar quem detém cada share e o threshold (3 de 5).
- **Auto-unseal** (KMS externo ou 2º OpenBao transit) como caminho para eliminar
  unseal manual e reduzir o SPOF operacional — decisão 🔴, possível evolução
  pós-MVP alinhada com Opção D (§1).

### 4.2 Backup da config + storage
- **Snapshot Raft** do OpenBao (cifrado; só restaurável com o mesmo unseal material):
```sh
bao operator raft snapshot save /openbao/data/snapshots/openbao-<timestamp>.snap
```
  Copiar o snapshot para **fora do volume/host** (destino sob decisão do Rodrigo). Sem snapshot restaurável, o volume é ponto único de falha.
- Backup **versionado em git** (já é o caso): policy HCL e comandos de bootstrap. A **chave em si nunca** entra em backup plaintext (por design).
- Como não há backup de VPS hoje (fato informado, appsell#88): **exigir** ao menos snapshot Raft + custódia do unseal antes do primeiro uso, **e** o backup mínimo da VPS resolvido no gate §3.0. 🔴

### 4.3 Teste de desastre (obrigatório antes de produção real)
- Em ambiente descartável: subir OpenBao limpo, restaurar snapshot, unseal com as
  shares, e provar que um ciphertext `vault:vN:*` **anterior** ainda descriptografa.
  Só então o DR está comprovado.

### 4.4 Quais bloqueios de `openbao-transit-local-ci.md:74-85` este plano fecha (escopo honesto — resolve MEDIUM)
- **Fecha:** storage durável + unseal real (§3.1-3.2); DR do unseal + snapshot + teste de desastre (§4); auth de workload durável (§1.2/§3.5).
- **Fecha por decisão explícita (não por entrega de HA):** operação **não-HA** no MVP (§1.1) — o item "cluster HA/Raft" é resolvido como *risco aceito*, não como cluster 3+ nós entregue.
- **NÃO fecha (registrado como aberto):** inventário/read-repair de versões de envelope (`:80`); staging isolado com observabilidade + SLO (`:81`). O item `:79` (reconciliação durável do callback Identity após consumo do code) **não** é DR e **não** é coberto aqui — pertence ao slice de login (#93), não a este.

---

## 5. Mudança de código no AppSell (a PR de certificação)

Objetivo: liberar o provider OpenBao em `NODE_ENV=production` **somente** sob
condições estritas e auditáveis, sem afrouxar nada para o resto. Toda mudança de
env/credencial recai **no serviço `web`** (BFF); `api` não é tocado.

### 5.1 O que a PR precisa conter
1. **Gate explícito no provider** (`openbao-transit-key-envelope-provider.server.ts:39-41`):
   substituir o `throw` incondicional por gate que exige flag de certificação
   **explícita e default-off** (ex.: `APPSELL_OPENBAO_PROD_CERTIFIED=1`) **E**
   endereço `https:` **E** credencial de workload presente. Sem a flag, mantém o
   `throw` (fail-closed). Um único ponto de decisão, testável — **sem** `if profile`
   espalhado (regra 1 Rodrigo).
2. **TLS + CA confiável:** garantir que `parseAddress` aceite o host interno
   (já aceita `https://<host>:<porta>` com `pathname==="/"`, sem query/hash/creds,
   linhas 160-179 — validar). **Documentar e exigir `NODE_EXTRA_CA_CERTS`** apontando
   para o CA bundle privado montado (§1.1); sem isso o `fetch()` falha handshake — e
   desabilitar verificação é proibido.
3. **Auth de workload (AppRole):** implementar login AppRole (unwrap do SecretID
   wrapped, `POST auth/approle/login`) + **re-login** antes do TTL de 15 min. Hoje o
   provider só usa `APPSELL_OPENBAO_TOKEN` estático (`fromEnvironment`, linha 68) —
   insuficiente para long-running. Maior peça nova de código.
4. **Config/flag** documentada em `.env.example` e nos env de prod (**só `web`**):
   `APPSELL_VAULT_PROVIDER=openbao-transit`, `APPSELL_OPENBAO_ADDR=https://openbao-90:8200`,
   mount/key, timeout, RoleID, entrega wrapped de SecretID, `NODE_EXTRA_CA_CERTS`.
   Nada de credencial de vault no `api`.
5. **Testes:** reusar `tests/openbao-transit-*.test.ts`; **adicionar** teste que prova
   que sem a flag o prod continua fechado, e que com `https` + flag + credencial abre;
   manter fail-closed (indisponibilidade, token inválido, protocolo errado, adulteração).
6. **Docs:** atualizar `docs/openbao-transit-local-ci.md` (ou novo `docs/openbao-transit-prod.md`) refletindo §4.4; runbook de unseal/DR/rotação de SecretID.
7. **Não tocar** o provider local: `token-vault.ts:183/:222` continua proibindo local em prod. Caminho de prod = **exclusivamente** OpenBao certificado, **só no `web`**.

### 5.2 Critérios de aceite do security-review (hard-assert, zero soft pass)
- [ ] Prod **fechado por default**: sem `APPSELL_OPENBAO_PROD_CERTIFIED`, o provider lança (teste prova).
- [ ] Prod exige `https:` **com CA confiável** (`NODE_EXTRA_CA_CERTS`); verificação TLS nunca desabilitada; `http` não-loopback recusado.
- [ ] DEK, SecretID, token de workload, unseal key, root token: **nunca** em log, erro ou corpo de request (estender `openbao-transit-provider.test.ts:92-97`).
- [ ] Policy do workload = só `encrypt`+`decrypt` da chave exata; leitura de metadados dá 403 (prova de integração).
- [ ] Token de workload é **curto (15m) + re-obtido via AppRole**; SecretID tem TTL/num_uses finitos (**nunca `0`/ilimitado**) e é entregue wrapped; rotação documentada.
- [ ] Credencial de vault **só no `web`**; nenhum path do `api` recebe RoleID/SecretID.
- [ ] Chave `exportable=false` confirmada; nenhum caminho tenta backup plaintext.
- [ ] AAD obrigatória mantida (wrap e unwrap); ciphertext valida versão declarada.
- [ ] Nenhum secret hardcoded; tudo via env/secret manager; `.env.example` sem valores reais.
- [ ] Sem gambiarra `if profile`/try-catch swallow; erro sanitizado e fail-closed.
- [ ] Cross-tenant: nada do gate afeta `flow`/`dados`; provider não lê chave de outro tenant.

Invocar antes do merge: `Appsell-issue-90:security-review` (auth/secrets) + reviewer específico + tester de prod (regras 4 e 9 do Rodrigo). CI gate ≠ review.

---

## 6. Rollback — **honesto sobre o que recupera e o que não recupera** (resolve HIGH 7)

**Distinção central:** existe um ponto sem volta — o **primeiro ciphertext
`vault:vN:*` persistido em prod**. Antes dele, rollback é rollback. Depois dele,
remover a flag **não é rollback funcional**: é **fail-closed de emergência**, e os
envelopes já cifrados ficam inacessíveis (o provider local não os decifra).

### 6.1 Rollback **pré-primeiro-write** (seguro, é rollback de verdade)
- Enquanto nenhum token OAuth real foi cifrado e persistido: remover
  `APPSELL_OPENBAO_PROD_CERTIFIED` do env do **`web`** (backup do env antes; nunca
  omitir campo `env`) → provider volta a lançar em prod, sistema fecha, **sem perda
  de dados**. Reverter o merge restaura o `throw` incondicional. **Parar** (não
  destruir) `openbao-90`.

### 6.2 "Rollback" **pós-persistência** = fail-closed, NÃO recuperação
- Depois que envelopes reais existem: desligar a flag **derruba o login** (decrypt
  indisponível). Os envelopes **não voltam** por reverter código — não é rollback,
  é parada segura. Chamar isso de rollback seria desonesto.
- **Recuperação real** nesse estado = **restaurar snapshot Raft + unseal material**
  (§4) e manter o OpenBao + a versão anterior do provider disponíveis
  (**forward-fix**), não reverter para o provider local.
- **Provider local como fallback só é válido em NÃO-produção** (sem envelopes
  reais). Em prod com dados, provider local não é caminho de volta.

### 6.3 O que rollback NUNCA faz
- Não apaga a chave Transit nem o storage (perda irreversível).
- Não toca `flow`/`dados`, DNS, Traefik, `sell.autonomia.site`.

### 6.4 Pré-condições documentadas antes de qualquer 🔴 de deploy
- Comando exato de restauração de env + ETA (rollback pré-write).
- Snapshot Raft salvo, custódia de unseal confirmada (§4) **e** rotador de SecretID de pé (§1.2) — sem isso não há cutover.
- Healthz pós-deploy: `bao status` unsealed + round-trip encrypt/decrypt + `/health/ready` do `web`.
- Safety-abort (regra 3 Rodrigo): se pós-deploy a contagem de env cair, o serviço não ficar healthy, ou breaker abrir → **abortar e restaurar** env do backup sem novo 🟢 (pré-primeiro-write) / acionar recuperação por snapshot (pós-persistência).

---

## 7. Índice de pontos que exigem aprovação do Rodrigo (🔴)

| # | Passo | Por que é 🔴 |
|---|---|---|
| 1 | §3.0 gate de recursos/disco + backup (appsell#88) | **bloqueante**: sem resolver disco ~81% e backup, não provisiona |
| 2 | §1/§2 `disable_mlock` vs cap `IPC_LOCK` | trade-off de segurança (chave em swap) |
| 3 | §1.2/§3.5 desenho e janela do rotador de SecretID | muda escopo da PR de código |
| 4 | §3.1 criar App Service `openbao-90` + volume + config/TLS mount | 1ª escrita em infra prod |
| 5 | §3.2 init/unseal (PGP) ou auto-unseal por KMS | gera material irrecuperável |
| 6 | §3.3-3.6 mount/chave/policy/AppRole/revogar root | config de secret/auth em prod |
| 7 | §4 DR (custódia unseal + snapshot + teste de desastre) | **pré-condição do 1º uso real** |
| 8 | §5 merge da PR de certificação | toca auth/secrets/vault (só `web`) |
| 9 | Deploy: flip de env **em `web`** | escrita de env em prod (backup antes, nunca omitir `env`) |

Nenhum item acima roda sem 🟢 explícito. Este documento é só o plano.

---

## 8. Próxima ação
1. Rodrigo decide §1 (confirmar A + CA privada) e §1.2/§3.5 (janela/rotador do SecretID).
2. Resolver o **gate 🔴 de disco + backup** (appsell#88) — pré-condição dura.
3. Abrir Issue de execução filha de #93 para a PR de código de certificação.
4. Só então: gate de recursos read-only (§3.0) e sequência 🔴 sob aprovação.

## Project update pendente
- Projeto: Autonom.ia Dev
- Status: In review (Rev 2 — 7 HIGH endereçados; aguardando re-review)
- Tipo: Docs / Infra-plan
- Prioridade: P0 (bloqueia login real)
- Risco: Alto (secrets, auth, prod multi-tenant, DR de material irrecuperável)
- Próxima ação: re-review codex + decisão do Rodrigo em §1/§1.2 e gate de disco (appsell#88)
- Ambiente: production (planejamento; nada executado)
