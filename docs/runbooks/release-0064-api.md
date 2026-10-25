# Plano — migrations 0062 a 0065 e a api nova em produção

> **Estado: só plano. Nada daqui foi executado.**
>
> - **Quem executa:** a trilha Plataforma, por SSH no host, uma fase por
>   vez. Cada fase marcada 🔴 precisa do OK do Rodrigo.
> - **Quem preparou:** a trilha 2 · Dinheiro, em 24/10/2026. A revisão
>   independente veio com 1 ALTO e 6 MÉDIOS. Todos foram incorporados. D1 e D2
>   foram decididas pela Orquestração em 24/10.
> - **0065 e pré-voo de donos (#509):** acrescentados pela trilha Plataforma em
>   25/10/2026. A 0065 entra na mesma leva, e o pré-voo roda antes e depois.
> - **D3 (#522):** desde 25/10, o migrador roda na máquina de quem opera. A
>   credencial dele nunca vai ao host.
> - **Base:**
>   - `release-437-correcoes-rce.md`: regras de shell e a volta guardada em
>     arquivo;
>   - `storage-461-rede-interna.md`: smoke da Hub2You e laço de
>     disponibilidade;
>   - `inventario-de-deploy.md`.

## Decisões (Orquestração, 24/10/2026)

- **D1 — o worker fica em `2759090`.**
  - O worker roda `appsell_api.asset_worker_service`.
  - Na cadeia de import dele, mudaram três arquivos:
    - `db.py`, só com uma função nova;
    - `main.py`, de onde o worker importa apenas `_pdf_sandbox_ready`, que não
      mudou;
    - `payout_routes.py`.
  - O comportamento é o mesmo, e a troca dele é `stop-first`.
- **D2 — o canal Financial sai do gate do `/health/startup` (#503), antes do
  release.**
  - **O problema.** O #466 (`c8fbca8`) pôs o canal no gate. Com loja vinculada e
    o canal ambíguo, a task que nunca ficou pronta respondia 503. A api tem 1
    réplica. Um reinício nesse estado (OOM, reboot, mudança de env, rotação do
    `secret_reference`) derrubaria a loja inteira, por causa de um canal que só
    afeta a entrega do Financial.
  - **O que fica no lugar.**
    - O canal vira campo informativo no `ready`, sem 503. O `startup` nem o
      consulta, porque a consulta levaria até 3 s, o timeout do HEALTHCHECK. A
      resposta pronta do `startup` é sempre `{"status":"ok","ever_ready":true}`.
    - O log `appsell.financial_channel.unresolved` sai em `error` no máximo uma
      vez a cada 5 min. Quem pergunta em regime é o monitor de dependências,
      porque ninguém sonda o `ready` da api em produção, e o `startup` em regime
      não consulta o banco.
  - **Fora de produção.** O gate nunca esteve no ar: a api roda `2759090`.
  - **Promoção de task com o canal quebrado.** Nada a impede: o `start-first`
    troca quando o `startup` fica verde, e o canal está fora desse gate. A Fase
    5 **detecta** depois da troca, pelo `financial_channel` no `ready`, e o freio
    é o rollback.

- **D3 — a credencial do migrador nunca vai ao host (#522).**
  - O alembic e o pré-voo rodam na máquina de quem opera, a partir de um
    checkout limpo do `SHA` mergeado, com as dependências do `uv.lock` daquele
    `SHA`.
  - Eles chegam ao Postgres por um túnel local, que só vive durante a janela
    (Fase 4).
  - O Postgres confere a senha por SCRAM: ela não passa em claro pelo host, e
    nada dela é gravado lá.
  - O código das migrations é o mesmo da imagem do `SHA`. A Fase 3 confere que
    as 4 migrations estão na imagem.

## O que sobe, e o que não muda

| Item | De | Para |
|---|---|---|
| Banco `appsell_native_90` | `0061_o_atraso_alcanca_o_aluno` (relido em 25/10) | `0065_dono_da_conta_payout` |
| `sell-autonomia_api` | `api:2759090` (lido em 24/10, ID `sha256:0e126a1a5b03…`) | `api:<SHA>` (Pré-condição 1) |
| `asset-worker` | `api:2759090` | **não muda** (D1) |
| `web`, `pdf-sandbox` | — | **não mudam** |
| Variáveis de ambiente | — | **nenhuma entra nem sai** |

### O que cada migration faz

- **0062 (#465).**
  - Cria `financial_channel()`, `financial_channel_integration()`,
    `financial_channel_ready()` e `map_financial_product()`.
  - Cria um trigger `after insert` em `catalog_product`.
  - O backfill só **acrescenta** um mapping `sha256(product.id)` para produto de
    loja vinculada, na integração da loja que tem o hash do canal, com
    `on conflict do nothing`.
  - Com loja vinculada e canal ambíguo, o backfill **aborta** a migration. Sem
    loja vinculada, ela não consulta o canal.
  - O `schema_ready_base_0016` passa a exigir o trigger ativo
    (`0062_mapping_do_produto_novo.py:254-262`). Desligar o trigger reprova o
    `ready` também da api atual.
- **0063 (#147).**
  - Cria só a leitura `checkout_reconciliation_report`.
  - O grant ao `appsell_reconciler` só acontece se o papel existir. Em produção
    ele não existe, porque o init de superusuário só roda em banco novo.
  - Criar o papel é mudança de permissão, com OK separado do Rodrigo. **Fica
    fora deste release.**
- **0064 (#382).**
  - Cria `resolve_seller_financial_activation()` e
    `activate_seller_financial()`.
  - Não liga nada. Sem `APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS`, que não
    existe na env de produção, o `GET` responde `{"self_service": false}` e o
    `POST`, 403.
  - A Hub2You fica de fora.
- **0065 (#509).**
  - Passa o dono de `public.seller_payout_account` para `appsell_owner`, só se
    ele for outro. Índices, toast e tipos acompanham a tabela.
  - Em produção a tabela nasceu com o migrador como dono. As funções de
    payout (`security definer`, dono `appsell_owner`) recebem
    `permission denied` nela, inclusive as da 0064. A 0065 conserta isso.
  - Ninguém perde acesso: o migrador é membro de `appsell_owner`, o runtime
    nunca teve privilégio direto, e o FORCE RLS e a policy não mudam.
  - Sai do `set local role` do `env.py` só pelo tempo do `alter`, como a 0053:
    só o dono atual transfere a posse. Por isso a migration tem de rodar com o
    **mesmo migrador que é o dono hoje**. Com outro, o `alter` falha e a
    transação desfaz as quatro.
  - O downgrade não devolve o dono.

### O pré-voo de donos

`python -m appsell_api.preflight_donos` lista os objetos de `public` e
`app_private` cujo dono não é `appsell_owner`, sem contar membros de extensão.
Só lê o catálogo, numa transação READ ONLY com `statement_timeout` de 5 s.
Sai com 0 sem nada fora, com 1 e a lista quando há algo fora, e com 2 em erro de
uso ou de banco. A URL vem de `APPSELL_PREFLIGHT_DATABASE_URL`, sem valor
padrão. O mesmo critério está no anexo A (`pre_voo_donos`).

Lido em produção em 25/10, só em leitura: **um item**, a própria
`seller_payout_account`. Todos os objetos do migrador no banco são dela (tabela,
índices, toast e tipos). O `alembic_version` pertence ao `appsell_owner`. As
funções do `pgcrypto` ficam de fora por serem membros de extensão.

### O que a api nova muda

- `/health/startup`: o canal não entra nem é consultado (#503).
- `/health/ready`: o campo `financial_channel`, sem mudar o status.
- Rotas de ativação: existem, mas desligadas.
- Rotas de payout existentes: só a tipagem de `_model_from_body`.

## Ordem: migration primeiro, depois a api

1. **A api atual (`2759090`) funciona sobre o banco em `0064`.** Evidência
   local, de 24/10:
   - **Catálogo comparado.** Num banco descartável, em 0061 e em 0064, comparei
     as funções (definição, dono, ACL), as colunas, os triggers e os grants de
     tabela. A diferença é só:
     - 7 funções novas;
     - 1 trigger novo;
     - as 2 bases do `schema_ready`, reescritas para aceitar as versões novas.

     Nenhuma coluna, grant de tabela ou função existente muda.
   - **Trigger ensaiado com o código atual.** O efeito que importa na api atual
     é o trigger em todo insert de produto:
     - o `catalog_routes.py` não mudou de `2759090` até a `main`;
     - o `test_postgres_financial_product_mapping.py` insere produto com o
       trigger ativo;
     - o caso de canal ambíguo está em
       `test_an_ambiguous_channel_never_blocks_product_creation`.
   - **Startup.** O `schema_ready()` fica no banco e aceita de 0057 a 0065,
     então o `/health/startup` da api atual continua verde.
   - **0065.** Só troca o dono de uma tabela e ensina a versão ao readiness.
     Para a api atual, o efeito é as funções de payout voltarem a ler e gravar
     a tabela.
   - **Confirmação no host.** A Fase 4 lê o `/health/ready` da api atual depois
     da migration.
2. **A api nova precisa da 0064.**
   - As rotas novas chamam funções da 0064.
   - Sobre um banco em 0061, a api nova sobe, porque o `schema_ready` em 0061
     aceita 0061, mas as rotas novas dão 500.
   - A ordem é do plano, não do código.
3. **A migration é um ponto de parada seguro.** A api atual pode ficar sobre o
   banco em 0064 o tempo que for preciso.

**Uma transação só.** O `env.py` tem um único `begin_transaction` para o
`upgrade head`. Ou entram as quatro migrations, ou nenhuma.

**Locks.**
- O `create trigger` pede `SHARE ROW EXCLUSIVE` em `catalog_product` e segura
  até o commit. Isso bloqueia escrita de produto, não a leitura da vitrine.
- No downgrade, o `drop trigger` pede `ACCESS EXCLUSIVE`, que bloqueia também a
  leitura.
- Para não enfileirar atrás de uma transação longa, upgrade e downgrade rodam
  com `lock_timeout=5s`, pelo `PGOPTIONS` do ambiente do comando (Fase 4).
  Conferido em 24/10: o psycopg do alembic recebe o valor.
- O `alter table … owner` da 0065 pede `ACCESS EXCLUSIVE` em
  `seller_payout_account` e segura até o commit da leva inteira. Nesse
  intervalo, leitura e escrita de conta de recebimento esperam. Hoje esses
  caminhos já falham com `permission denied`.
- Se o lock não sair em 5 s, a transação volta inteira e pode ser repetida.

## Bloqueios antes de qualquer fase 🔴

- **B1 — credencial do migrador (#456): respondida em 25/10.**
  - Os releases de setembro tiravam a URL do migrador da spec do
    `sell-autonomia_bootstrap-native-90`. Esse serviço **não existe mais** no
    Swarm (leitura de 23/10).
  - Hoje a credencial vive na máquina de quem opera. O lugar está no registro
    privado, nunca no repo.
  - O papel dela é o mesmo que hoje é dono de `seller_payout_account`, como a
    0065 exige.
  - Foi usada em 25/10, só em leitura, pelo túnel da Fase 4.
- **B2 — CI no SHA do release.**
  - É preciso um `workflow_dispatch` do App CI no `SHA`, com `cert_api`,
    `cert_postgres` e o `ci-gate` em success. O `cert_postgres` roda a cadeia
    `0062→0063→0064→0065` e o teste do drift de dono
    (`test_postgres_dono_da_conta_payout.py`).
  - O run 40110451823 vale para `e31e37a`, o conteúdo do #469. O `SHA` da
    `main` é outro commit e precisa de run próprio.

## Pré-condições, no repositório, sem tocar o host

1. **SHA.** É a `main` no momento do OK, escrita por extenso no comentário da
   janela.
   - Hoje é `c9f2a71`. De `e3c38d2` até ele há só docs:
     `git diff --stat e3c38d2 c9f2a71 -- apps/api deploy infra output` sai
     vazio.
   - Se a `main` andar, repetir o diff. Qualquer linha nesses caminhos obriga a
     reavaliar.
   - O `SHA` só existe depois do merge do PR do #503 e do PR do #509. O
     `workflow_dispatch` do B2 roda nele.
2. **A volta, ensaiada em 24/10 em banco local.**
   - Cadeia: `upgrade 0061` → `upgrade head` →
     `downgrade 0061_o_atraso_alcanca_o_aluno` → `upgrade head`.
   - Todos os passos deram exit 0.
   - O `schema_ready()` deu `t` em cada parada, também como `appsell_runtime`.
   - Depois do downgrade sobraram 0 das 7 funções, 0 trigger e 0 funções de
     `app_private` fora do dono.
   - No CI, cada downgrade rodou quando a sua migration era a cabeça (#466, #459,
     #469, #509).
   - **0065, ensaiada em 25/10 em banco local.** Com o drift recriado em 0064
     (dono = migrador), o `upgrade head` levou tabela, índices e tipo ao
     `appsell_owner`. O `downgrade -1` manteve o dono, e o `upgrade` de novo
     voltou à cabeça.
3. **Leitura em arquivo local.**
   - O SQL de leitura (anexo A) e o de conferência (anexo B) ficam no
     computador de quem opera. Cada bloco é copiado para `anexo-A.sql` e
     `anexo-B.sql`, sem a cerca, terminando em quebra de linha. O `shasum -a 256`
     de cada arquivo tem de bater com o título do anexo.
   - Eles vão pelo stdin do `ssh`, e nada é gravado no host.
   - Os dois foram testados em banco local, em 0061 e em 0064, com exit 0. Em
     25/10, na versão com a 0065, foram testados em 0065 com e sem o drift de
     dono. Com o drift, o anexo A dá `pre_voo_donos` 1 e o anexo B para com
     `permission denied` no `owner_le_payout`, o que na Fase 4 é abort.
   - Sem dado vinculado, a contagem do backfill só foi provada em sintaxe. A
     prova com dado é a Fase 4.

   ```sh
   leitura() {   # $1 = arquivo .sql local
     : "${ALIAS:?}"; ssh "$ALIAS" 'sudo -n docker exec -i "$(sudo -n docker ps -q --filter name=sell-autonomia_postgres-native-90. | head -1)" psql -U appsell_native_admin -d appsell_native_90 -At -v ON_ERROR_STOP=1' < "$1"
   }
   ```

## Regras de shell no host

- São as do `release-437-correcoes-rce.md`:
  - `sudo -n` em todo `docker`;
  - variável literal, guardada com `: "${VAR:?}"`;
  - nenhum `rm` com curinga;
  - proibidos até a Fase 6: `docker image prune`, `system prune`,
    `volume prune`, `rmi` e `builder prune --all`.
- **Valores do host ficam fora do repo**, que é público. Nada de IP, alias ou
  usuário SSH, nem caminho no host. Eles vêm do registro privado de quem opera:
  - `ALIAS`, na máquina local: o alias SSH do host;
  - `PASTA`, no host: a pasta deste release, com valor literal, fora de
    qualquer pasta de projeto. Primeira linha de cada sessão:
    `: "${PASTA:?PASTA vazio — parar}"`.
- Desvio declarado em relação ao #437: os `rm` deste plano usam `$PASTA`. Só
  apagam **arquivo**, nunca pasta, sempre depois do guard de `PASTA` e de um
  `test -f` no mesmo caminho.
- Spec e env só com `--format` de campos. Nada de `cat`.
- Variável só por nome.
- Log só por contagem:
  `2>&1 | grep -Eic 'error|traceback|financial_channel.unresolved'`.
- **A credencial do migrador nunca vai ao host** (D3). Na máquina local, ela
  também não vai por argumento nem para a tela. Ela entra só no ambiente do
  processo do alembic ou do pré-voo (Fase 4).

## Fase 0 — leitura do host no dia 🔴 (só leitura, OK do Rodrigo)

**Parâmetro | lido hoje | aplicado.**
- A coluna "Lido hoje" é preenchida na Fase 0 e postada no comentário da
  janela.
- A Orquestração confere cada linha contra a leitura.
- Divergência sem justificativa bloqueia.

| Parâmetro | Último registro | Lido hoje | Aplicado |
|---|---|---|---|
| imagem da api (`revision`) | `2759090` | | `api:<SHA>` |
| ID da imagem da api | `sha256:0e126a1a5b03…` (prefixo) | | — (vira a volta) |
| `UpdateConfig.Order` da api | `start-first` | | **não passar** `--update-order` |
| `UpdateConfig.FailureAction` / `Monitor` | `pause` / `5s` | | não muda |
| `RollbackConfig.Order` da api | `stop-first` | | não muda (a volta é por `--image`) |
| `StopGracePeriod` da api | `5m30s` | | não muda |
| healthcheck da api | o da imagem (`/health/startup`) | | o da imagem nova (mesmo caminho) |
| réplicas da api | 1 | | não muda |
| env da api: contagem e nomes | (gravar) | | idêntica, por nome |
| `APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS` | ausente | | **ausente** |
| `APPSELL_FINANCIAL_SHARED_OPERATOR_ID` / `_CATALOG_ITEM_ID` | ausentes | | **ausentes** |
| `APPSELL_FINANCIAL_ACTIVATION_REQUIRES_PAYOUT_ACCOUNT` | ausente | | **ausente** (valor inválido derruba o GET com 503) |
| imagem e estado do `asset-worker` | `2759090` | | não muda |
| `alembic_version` | `0061_o_atraso_alcanca_o_aluno` | | `0065_dono_da_conta_payout` |
| `pre_voo_donos` | 1: `public.seller_payout_account` (25/10) | | 0 |
| `dono_payout` | o migrador (25/10) | | `appsell_owner` |
| `schema_ready()` como runtime | `t` (04/09) | | `t` |
| `identidades_do_canal` | 1 (esperado) | | 1 (exigido) |
| `lojas_vinculadas` / `…_com_hash_do_canal` | ? / ? | | — |
| `linhas_que_o_backfill_grava` | ? | | 0 depois da migration |
| `mappings_total` | ? | | antes + `linhas_que_o_backfill_grava` (diferença explicada) |
| `dead_letter_sem_mapping` | ? | | igual |
| `fora_do_dono` | ? | | igual |
| `reconciler` | 0 (esperado) | | 0 |
| `admin_ignora_rls` | `t` (esperado: o admin nasce como `POSTGRES_USER`) | | `t` (exigido) |
| onde está a credencial do migrador | **a confirmar (#456)** | | (só o nome do lugar) |
| memória `available` / disco livre | — | | ≥ 4 GiB / ≥ 15 GiB |

Leitura dos serviços:

```sh
dig +short sell.autonomia.site                 # o mesmo IP de `ssh -G "$ALIAS" | grep '^hostname '`
ssh "$ALIAS"
sudo -n true || echo "PARAR: sudo -n não liberado neste host"
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  sudo -n docker service inspect "$s" --format \
    "$s image={{.Spec.TaskTemplate.ContainerSpec.Image}} replicas={{.Spec.Mode.Replicated.Replicas}} update={{json .Spec.UpdateConfig}} rollback={{json .Spec.RollbackConfig}} grace={{.Spec.TaskTemplate.ContainerSpec.StopGracePeriod}} hc={{json .Spec.TaskTemplate.ContainerSpec.Healthcheck}}"
  sudo -n docker service inspect "$s" --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' \
    | python3 -c 'import json,sys;n=sorted(e.split("=",1)[0] for e in json.load(sys.stdin));print(len(n));print("\n".join(x for x in n if x.startswith("APPSELL_FINANCIAL")))'
  sudo -n docker service ps "$s" --filter desired-state=running --format "$s {{.Image}} {{.CurrentState}} {{.Error}}"
done
sudo -n docker image inspect easypanel/sell-autonomia/api:2759090 --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
free -h ; df -h / /var/lib/docker
```

Leitura do banco, na máquina local: `leitura anexo-A.sql`. O SQL repete o
critério de `financial_channel()` e o predicado exato do `_BACKFILL` da 0062:
join pelo hash do canal, sem filtro de nome, e `not exists` pela chave primária
do mapping. Se o `set local role appsell_runtime` falhar, a leitura para com
erro. Nesse caso, ler o `schema_ready()` como admin e anotar o desvio.

**Abort antes de começar:**
- a api fora de `2759090` ou com outro ID;
- `UpdateConfig.Order` da api diferente de `start-first`;
- `alembic_version` diferente de 0061;
- `schema_ready` `f`;
- `admin_ignora_rls` `f`: com o FORCE RLS, todas as contagens seriam 0 sem erro;
- `identidades_do_canal` ≠ 1, com `lojas_vinculadas` > 0: a 0062 abortaria, e o
  caso é do Rodrigo;
- `pre_voo_donos` com qualquer objeto além de `public.seller_payout_account`:
  cada item novo se decide antes da janela (corrigir numa migration, aceitar no
  pré-voo, ou bloquear), nunca no dia;
- `dono_payout` diferente do papel do migrador do B1: a 0065 falharia no
  `alter`;
- alguma `APPSELL_FINANCIAL_*` já presente;
- memória ou disco abaixo do limite;
- B1 ou B2 sem resposta, ou o PR do #503 fora da `main`.

## Smoke — o mesmo antes e depois de cada fase que escreve

É o script Python da "Linha de base" do `storage-461-rede-interna.md`, rodado na
máquina local, sem sessão. Esperado:
- vitrine 200, com 2 cards, os dois preços e 0 imagens com erro;
- cada uma das duas páginas: 200, `comprar True`, `href True`, `login 307`,
  `return_to True`, `new_user True` e `identity 200`.

Junto roda o **laço de disponibilidade** do mesmo runbook, aberto antes de cada
escrita. A saída do "antes" vai para o comentário da janela. Se ela divergir do
esperado, não começar.

## Fase 1 — preservar a volta da api 🔴 (escreve no host)

É a Fase 1a do #437, aplicada à imagem da api. Valores literais, vindos da
Fase 0:

```text
ROLLBACK_IMAGE = easypanel/sell-autonomia/api:2759090
ROLLBACK_ID    = <ID completo lido na Fase 0>
ROLLBACK_TAG   = easypanel/sell-autonomia/api:rollback-0064-2759090
PASTA          = <do registro privado de quem opera>
```

1. `install -d -m 700` da pasta.
2. `docker tag` do ID na `ROLLBACK_TAG` e `image inspect`, conferindo ID e
   `revision`.
3. Campos da spec, sem env: `api.spec-campos.antes` e
   `worker.spec-campos.antes`.
4. Nomes da env da api: `api.env-names.antes`.
5. `docker save` das duas tags em `api-2759090.tar`, mais `sha256sum` e a prova
   pelo `manifest.json`.

O prune das 00:39 apaga, depois de 24 h, imagem sem contêiner. Por isso a volta
fica no `.tar`.

**Abort:** ID ou label que não batem, ou `save` que falhou.

## Fase 2 — dump do banco 🔴 (escreve no host)

```sh
: "${PASTA:?PASTA vazio — parar}"
TS=<literal AAAAMMDDTHHMMSSZ>
: "${TS:?}"
: "${PASTA:?}"
D="$PASTA/appsell_native_90_pre0064_$TS.dump"
PG="$(sudo -n docker ps -q --filter name=sell-autonomia_postgres-native-90. | head -1)"
: "${PG:?postgres sem contêiner — parar}"
( umask 077; sudo -n docker exec "$PG" pg_dump -U appsell_native_admin -Fc appsell_native_90 > "$D" 2> "$D.err" ); echo "pg_dump rc=$?"
test ! -s "$D.err" || echo "PARAR: pg_dump escreveu em stderr"
sudo -n docker exec -i "$PG" pg_restore -f /dev/null < "$D"; echo "pg_restore rc=$?"   # lê o arquivo inteiro
ls -l "$D" ; sha256sum "$D"
```

O dump tem dado pessoal. Fica no host, com `600`, e não sai de lá.

**Abort:**
- `pg_dump rc` ≠ 0 ou stderr não vazio;
- `pg_restore rc` ≠ 0;
- tamanho fora do esperado perto do último dump registrado.

## Fase 3 — a imagem da api 🔴 (escreve no host)

É a Fase 1b do #437:
- fonte pelo `git archive` do `SHA`, direto pelo `ssh`, com sha256 conferido dos
  dois lados;
- BuildKit próprio, com 3 GiB e 2 núcleos, e o `trap` que remove o builder;
- observador `free -h -s 10`.

Muda só:
- `-f .../deploy/easypanel-native/Dockerfile.api`;
- `--build-arg GIT_SHA="$SHA"`;
- `-t "easypanel/sell-autonomia/api:$SHA"`.

Fonte em `$PASTA/src`. O `src.tar.gz` também fica no host, como prova do que
foi buildado. O downgrade roda do checkout local (D3).

```sh
: "${PASTA:?PASTA vazio — parar}"
: "${SHA:?}"
sudo -n docker image inspect "easypanel/sell-autonomia/api:$SHA" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'   # = SHA
sudo -n docker run --rm --entrypoint python "easypanel/sell-autonomia/api:$SHA" -c \
  'import appsell_api.db as d, appsell_api.payout_routes as p, appsell_api.preflight_donos as v; print(hasattr(d,"financial_channel_ready"), p._SELF_SERVICE_SELLERS_ENV, v.DONO)'
# True APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS appsell_owner
ls "$PASTA"/src/apps/api/migrations/versions/ | grep -c '^006[2345]_'   # 4
sudo -n docker image inspect easypanel/sell-autonomia/api:rollback-0064-2759090 --format '{{.Id}}'   # = ROLLBACK_ID
```

**Abort:**
- build morto, ou memória abaixo de 4 GiB;
- label diferente do `SHA`;
- saída diferente da esperada;
- tag de volta sumida.

Até aqui, nada em produção mudou.

## Fase 4 — migration 🔴 (escreve no banco)

A api atual segue servindo.

**1. Checkout local, na máquina de quem opera.** Nada desta etapa toca o host.

```sh
: "${SHA:?}"
: "${CHECKOUT:?}"   # pasta local nova, do registro privado
git worktree add --detach "$CHECKOUT" "$SHA"
cd "$CHECKOUT/apps/api"
uv sync --frozen          # as dependências do uv.lock deste SHA
git status --porcelain    # vazio
.venv/bin/alembic heads   # 0065_dono_da_conta_payout (head)
```

**2. O túnel.** `com_tunel <comando>` vem do registro privado de quem opera e
faz quatro coisas:
1. abre uma porta em `127.0.0.1`, ligada por `ssh` ao contêiner da api atual
   e dali ao Postgres;
2. monta `APPSELL_MIGRATOR_DATABASE_URL`
   (`postgresql+psycopg://…@127.0.0.1:<porta>/appsell_native_90?…&require_auth=scram-sha-256`)
   **só no ambiente do comando**, lendo a senha do lugar do B1. A senha não
   aparece em argumento, tela, histórico ou arquivo. O `require_auth` faz o
   libpq recusar a conexão se o servidor pedir outro método que não SCRAM. Sem
   a credencial, ele aborta com `rc=2` antes de rodar qualquer coisa: uma URL
   vazia faria o alembic cair na do `alembic.ini`;
3. roda o comando e, durante ele, conta o argv com URL (passo 3);
4. derruba o túnel e confere que não sobrou processo, nem local nem no host.

Cada túnel aceita **uma conexão**. O `env.py` usa `NullPool` e uma transação
só, então o `upgrade head` cabe numa conexão. Um túnel por comando.

Os timeouts de sessão vão pelo `PGOPTIONS`, que o libpq do psycopg lê:

```sh
export PGOPTIONS="-c lock_timeout=5s -c statement_timeout=120s"
```

**3. Ensaio em leitura**, logo antes, na mesma sessão. Ele prova o método de
autenticação e que a URL não aparece em argumento de processo:

```sh
cd "$CHECKOUT/apps/api"
# require_auth existe a partir do libpq 16 (160000).
.venv/bin/python -I -c 'import psycopg; print(psycopg.pq.version())'   # >= 160000
PGOPTIONS="$PGOPTIONS -c default_transaction_read_only=on" \
  com_tunel .venv/bin/alembic current      # 0061_o_atraso_alcanca_o_aluno
pre_voo() {
  com_tunel sh -c 'APPSELL_PREFLIGHT_DATABASE_URL="$APPSELL_MIGRATOR_DATABASE_URL" exec .venv/bin/python -m appsell_api.preflight_donos'
  echo "pre_voo rc=$?"
}
pre_voo   # antes: rc=1, só public.seller_payout_account
```

Durante cada comando, o `com_tunel` conta, no local e no host, as linhas de
`ps -eo args` com URL de banco (esquema `postgres…://`, ou `://…@127.0.0.1`).
Tem de dar **0** nos dois lados. O padrão é o esquema de URL, e não a palavra
solta: o caminho de binário do Postgres (`…/postgresql@16/…`) e um `ssh
usuario@127.0.0.1` de outro serviço casariam e não são URL. O contador foi
provado com um processo falso de URL no argv: deu 1 no local e no host, e 0
quando ele saiu.

Ensaiado em 25/10 a partir do #510 (885a611), em leitura:
- libpq `180000`;
- `alembic current` deu `0061_o_atraso_alcanca_o_aluno`;
- o pré-voo deu `rc=1`, só com a tabela;
- 0 linhas de argv com URL, no local e no host;
- túnel derrubado, sem processo sobrando;
- controle negativo: exigir `require_auth=md5` falhou com `server requested
  SASL authentication`, então o servidor autentica por SCRAM.

- `rc=2` no pré-voo é erro de conexão, não drift: parar e entender antes de
  seguir.
- `rc=0` antes do upgrade quer dizer que o dono já mudou desde a Fase 0, por
  outra mão: parar, reler a Fase 0 inteira e não seguir sem entender.
- `alembic current` diferente de 0061: parar.

**Leitura de antes.** Repetir `leitura anexo-A.sql` logo antes do upgrade, e
guardar os números. Produto criado entre a Fase 0 e este passo muda a conta.
O `pre_voo_donos` tem de seguir em 1, com a mesma tabela.

**4. Upgrade**, na mesma sessão:

```sh
cd "$CHECKOUT/apps/api"
com_tunel .venv/bin/alembic upgrade head; echo "alembic rc=$?"
pre_voo   # depois: rc=0, {"status":"ok",...}
```

Esperado:
- quatro `Running upgrade` (0061→0062, 0062→0063, 0063→0064, 0064→0065);
- `alembic rc=0`;
- `pre_voo rc=0`;
- sem `ERROR` nem `Traceback`;
- o túnel derrubado depois de cada comando, sem processo sobrando.

**Se o túnel cair no meio do upgrade**, a conexão fecha e o Postgres desfaz a
transação inteira. Antes de qualquer nova tentativa, conferir com um túnel
novo, em leitura, que nada entrou:

```sh
PGOPTIONS="$PGOPTIONS -c default_transaction_read_only=on" \
  com_tunel .venv/bin/alembic current      # tem de seguir 0061
```

Só então repetir, e só com novo OK. Qualquer outra versão: parar e levar ao
Rodrigo.

**Se a máquina local cair** (antes, durante ou depois do upgrade):
1. noutra máquina de quem opera, refazer o passo 1 com o mesmo `SHA`. O
   `git status --porcelain` tem de dar vazio e o `alembic heads`, 0065;
2. preparar o `com_tunel` e o lugar da credencial do registro privado;
3. rodar o ensaio do passo 3. O `alembic current` diz onde o banco está: 0061
   (nada entrou) ou 0065 (a leva entrou inteira). Nunca há meio-termo, porque é
   uma transação só;
4. seguir do ponto que o `alembic current` mostrar, com novo OK.

A mesma volta vale para o Rollback 2.

**5. Conferência.** Na máquina local, rodar `leitura anexo-B.sql` e depois
`leitura anexo-A.sql`. Esperado:
- `versao` = `0065_dono_da_conta_payout`;
- `schema_ready` = `t` e `canal_pronto` = `t`;
- `dono_payout` = `appsell_owner` e `owner_le_payout` = `t`;
- `pre_voo_donos` = 0;
- `fora_do_dono` igual ao da Fase 0;
- `trigger` = `O`;
- `linhas_que_o_backfill_grava` = 0;
- `mappings_total` = o valor do passo 2 mais o `linhas_que_o_backfill_grava` do
  passo 2. Diferença só se explicada por produto criado no meio;
- `dead_letter_sem_mapping` igual ao do passo 2.

Na **api atual**, de dentro da task Running, o `/health/ready` tem
`status: ok` e `database: true`.

O `asset-worker` continua com 1 task `Running`, e o log dele mostra contagem 0.

O log da api também mostra contagem 0, e o smoke fica igual à linha de base.

**Safety abort:**
- Se a migration falhar, a transação desfaz tudo e o banco segue em 0061.
  - Uma falha `must be owner of table seller_payout_account` quer dizer que o
    migrador do B1 não é o dono atual (ver Fase 0, `dono_payout`).
  - Conferir que `versao` = 0061 e parar.
  - Se a falha for `lock_timeout`, pode repetir uma vez, com novo OK.
- Se a migration passar e alguma conferência falhar, parar sem trocar a api e
  levar o achado ao Rodrigo.
- O downgrade não é automático (ver Rollback).

**Ponto de parada.** A produção pode ficar aqui. Seguir é outro OK.

## Fase 5 — trocar a api 🔴

```sh
: "${SHA:?}"
sudo -n docker service update --image "easypanel/sell-autonomia/api:$SHA" sell-autonomia_api
```

Só `--image`. Ordem, failure action, monitor e graça ficam os da spec,
conferidos na Fase 0 (`start-first`).

Passar uma `--update-order` diferente da lida é proibido. A lição é a do #461:
22 s de 500 com `stop-first`.

A task nova sobe ao lado da antiga e só a substitui quando o `/health/startup`
dela fica verde. O claim do e-mail tem fence no banco, então as duas tasks
juntas não duplicam envio.

Validação, nesta ordem:
1. **Task nova.** Durante o `start-first` há duas tasks `running`. A nova é a
   única na imagem do `SHA`:

   ```sh
   : "${PASTA:?PASTA vazio — parar}"
   : "${SHA:?}"
   sudo -n docker service ps sell-autonomia_api --filter desired-state=running \
     --format '{{.ID}} {{.Image}}' | grep -F "api:$SHA" > "$PASTA/task-nova"
   test "$(wc -l < "$PASTA/task-nova")" -eq 1 || echo "PARAR: task nova ausente ou ambígua"
   task=$(cut -d' ' -f1 "$PASTA/task-nova")
   cid=$(sudo -n docker inspect --format '{{.Status.ContainerStatus.ContainerID}}' "$task")
   : "${cid:?task nova sem contêiner}"
   ```
2. **Imagem e saúde.** A task tem de ser `api:$SHA`, `healthy` em até 3 min.
   `UpdateStatus.State` = `completed` em até 7 min: a graça de 330 s da task
   antiga conta. `PreviousSpec` = `api:2759090`.
3. **Env.** Nomes idênticos aos de `api.env-names.antes`, por `diff`, sem valor.
4. **Ready, 3 leituras com 15 s entre elas.** De dentro do contêiner do item 1:

   ```sh
   for leitura in 1 2 3; do
   sudo -n docker exec "$cid" python -c 'import json,urllib.request,urllib.error
   try:
       r=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health/ready",timeout=5))
   except urllib.error.HTTPError as e:
       print("ABORT ready", e.code); raise SystemExit(1)
   a=r.get("antivirus") or {}
   print(r.get("status"), r.get("database"), r.get("storage"), r.get("pdf_sandbox"), a.get("saudavel"), r.get("financial_channel"))'
   test "$leitura" -eq 3 || sleep 15
   done
   # esperado: ok True True True True True
   # o 5º campo pode vir None só na 1ª leitura: o `antivirus` aparece depois da
   # primeira verificação (main.py, ready). Na 3ª leitura tem de ser True.
   ```

5. **Log.** `docker service logs --since 5m sell-autonomia_api` com contagem 0.
6. **Loja.** Smoke idêntico à linha de base, e o laço sem nenhuma linha
   diferente de 200.
7. **Canal.** O `financial_channel` do item 4 detecta, depois da troca, a task
   promovida com o canal quebrado; o `startup` não o barra (#503). `false` é
   abort, com o rollback da api.
8. **Ativação desligada.** A prova é a env sem `SELF_SERVICE` (item 3). A
   chamada da rota exigiria sessão de vendedor e não é feita.

**Safety abort, com rollback da api imediato e sem novo OK:**
- task nova sem `healthy` em 3 min;
- `UpdateStatus` em `paused`, `rollback_*` ou sem `completed` em 7 min;
- env com nome a mais ou a menos;
- ready com HTTP de erro, ou com qualquer valor diferente do esperado;
- log com contagem acima de 0;
- qualquer linha do smoke diferente da linha de base.

## Fase 6 — fechar

- Registrar no `PRODUCTION_CURRENT.md` e no inventário o que o host respondeu:
  imagens com ID e `alembic_version`.
- Apagar a pasta `src` por caminho exato, com `lsof +D` vazio, como na Fase 3
  do #437. O `src.tar.gz` fica.
- Confirmar que nenhum túnel sobrou, nem na máquina local nem no host. O
  `CHECKOUT` local fica até o fim das 24 h, porque o Rollback 2 roda dele;
  depois sai por `git worktree remove`.
- **Ficam até 24 h estáveis, e novo OK:**
  - `api-2759090.tar` com o `.sha256`;
  - `src.tar.gz`;
  - o dump;
  - os `.antes`.

  A volta da api precisa do `.tar`. O downgrade precisa do `CHECKOUT` local
  (Fase 4). O dump tem dado pessoal:
  prazo e remoção são do Rodrigo, por caminho exato.
- **Fora deste release, cada um com OK próprio:**
  - **o web do #469** (cartão de ativação e rota
    `app/api/financial/seller-activation`). O web em produção, `2d2e64f`, não
    tem o cartão. Ele vem **depois** desta api, porque a rota nova do web chama a
    api nova;
  - ligar a ativação para alguma loja (env `SELF_SERVICE` e o par `SHARED_*`);
  - criar o `appsell_reconciler` e agendar a conciliação (#147);
  - reprocessar o `dead_letter` antigo.

## Rollback

**1. A api, ETA de 2 a 7 min.** A graça de 330 s conta.

Primeiro, a conferência de tag e arquivo do #437: `image inspect` da
`ROLLBACK_TAG`. Se ela der "No such image", rodar `sha256sum -c` e
`docker load` do `api-2759090.tar`.

```sh
sudo -n docker service update --image easypanel/sell-autonomia/api:rollback-0064-2759090 \
  --update-failure-action pause sell-autonomia_api
```

- Sem `--update-order`: vale o `start-first` da spec, e a loja não cai.
- **Não usar `docker service rollback`.** A `RollbackConfig` é `stop-first`, e
  a `PreviousSpec` pode apontar para imagem já apagada pelo prune.
- A api `2759090` roda sobre o banco em 0064. **Voltar a api não mexe no
  banco.**

**2. O banco: só se a causa for uma migration, e com OK do Rodrigo.** Um exemplo
é o trigger da 0062 travando a criação de produto. Faz-se com a api já em
`2759090`.

- Usar o mesmo checkout local do `SHA` e o mesmo túnel da Fase 4 (D3). O
  downgrade é o código das migrations desse `SHA`, e a credencial não vai ao
  host.

```sh
cd "$CHECKOUT/apps/api"
export PGOPTIONS="-c lock_timeout=5s -c statement_timeout=120s"
com_tunel .venv/bin/alembic downgrade 0061_o_atraso_alcanca_o_aluno; echo "rc=$?"
PGOPTIONS="$PGOPTIONS -c default_transaction_read_only=on" \
  com_tunel .venv/bin/alembic current      # 0061_o_atraso_alcanca_o_aluno
```

ETA de 1 a 2 min. O `drop trigger` pode precisar de repetição por causa do
`lock_timeout`.

Esperado:
- `rc=0`, com 0065→0064, 0064→0063, 0063→0062 e 0062→0061;
- depois, pelo anexo B: `versao` = 0061 e `schema_ready` = `t`;
- 0 das 7 funções e 0 trigger;
- `dono_payout` continua `appsell_owner`: o downgrade da 0065 não devolve o
  dono, porque isso recriaria o defeito.

**Voltar só o dono**, se algum dia for preciso. Exige OK do Rodrigo e desfaz a
correção: as funções de payout voltam a dar `permission denied`. ETA abaixo de
1 min, como admin, numa transação só:

```sql
begin;
set local lock_timeout = '3s';
alter table public.seller_payout_account owner to <migrador do B1>;
commit;
```

Ensaiado em 24/10.

O `drop trigger` pede `ACCESS EXCLUSIVE` em `catalog_product`. Com
`lock_timeout=5s`, ele falha em vez de travar a vitrine. Repetir até entrar.

O downgrade **preserva** os mappings gravados: apagá-los quebraria compra que já
depende deles. Ele só tira funções e trigger.

**O `pg_restore` do dump da Fase 2 não é rollback.** Ele apaga toda escrita
feita depois do dump, inclusive compra e acesso. Só o Rodrigo decide, e só em
caso de dano de dado.

**3. Depois de qualquer volta:**
- smoke;
- sintoma, horário e saída no comentário da janela;
- `PRODUCTION_CURRENT.md` com o que o host responde.

## Riscos

| Risco | O que pega | Efeito na loja |
|---|---|---|
| Reinício futuro com o canal ambíguo | #503: o canal não entra no gate do `startup`; o log `financial_channel.unresolved` sai em `error` | nenhum: a task sobe e serve, e só a entrega do Financial fica afetada |
| Canal ambíguo hoje | Fase 0, e a própria 0062, que aborta | nenhum: a migration não entra |
| Backfill grava mapping | só acrescenta `sha256(product.id)`, com `on conflict do nothing`; conferência da Fase 4 | nenhum: a compra sem mapping já ia para `dead_letter` |
| Trigger trava criação de produto | o trigger rebaixa o erro de canal a `WARNING`; o downgrade tira o trigger | criar produto continua |
| Lock na migration | `lock_timeout=5s`: falha e volta, sem fila | escrita de produto espera até 5 s; a vitrine lê |
| Lock no downgrade (`ACCESS EXCLUSIVE`) | `lock_timeout=5s` | leitura de produto espera até 5 s |
| Api nova reprova no startup por dependência (banco, storage, sandbox) | `start-first`: a antiga segue servindo e o update pausa | nenhum |
| Credencial do migrador perdida (#456) | B1 | bloqueia o release |
| 0065 rodada com outro papel que não o dono atual | Fase 0 (`dono_payout`) e a própria 0065, que falha no `alter` | nenhum: a transação desfaz a leva |
| Lock da 0065 em `seller_payout_account` | `lock_timeout=5s` | conta de recebimento espera até o commit; hoje esses caminhos já falham |
| Outro objeto com dono errado aparece no dia | Fase 0 (`pre_voo_donos`) | nenhum: a janela não começa |
| Credencial exposta | D3: fica na máquina de quem opera, só no ambiente do comando; o Postgres a confere por SCRAM, e ela nunca vai ao host | — |
| O túnel cai no meio do upgrade | transação única: o Postgres desfaz a leva; `alembic current` = 0061 antes de repetir, com novo OK | nenhum: a api atual segue sobre 0061 |
| Túnel esquecido aberto | `com_tunel` derruba e confere ao fim de cada comando; a Fase 6 confere de novo | — |

## Anexo A — `anexo-A.sql` (leitura, sha256 `b433f3daa84ca68173bf6dfa4d05b0a23ceced59dab17596f216b6f103add974`)

```sql
begin transaction read only;
-- FORCE ROW LEVEL SECURITY nas tabelas lidas: sem superusuário ou BYPASSRLS,
-- as contagens saem 0 sem erro e a Fase 0 mentiria.
select 'admin_ignora_rls', rolsuper or rolbypassrls from pg_roles where rolname=current_user;
select 'versao', version_num from alembic_version;
set local role appsell_runtime;
select 'schema_ready', app_private.schema_ready();
reset role;
-- o mesmo critério de app_private.financial_channel() (0062)
with canal as (
  select distinct endpoint.endpoint_key_sha256, endpoint.secret_reference
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  where integration.adapter='generic_hmac_v1' and integration.display_name='Financial checkout'
    and integration.status='active' and endpoint.status='active')
select 'identidades_do_canal', count(*) from canal;
select 'lojas_vinculadas', count(*) from public.seller_organization
  where status='active' and financial_operator_id is not null and financial_product_catalog_item_id is not null;
-- o mesmo critério de app_private.financial_channel_integration() (0062):
-- endpoint e integração ativos da loja com o hash do canal, de qualquer adapter
with canal as (
  select distinct endpoint.endpoint_key_sha256, endpoint.secret_reference
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  where integration.adapter='generic_hmac_v1' and integration.display_name='Financial checkout'
    and integration.status='active' and endpoint.status='active')
select 'lojas_vinculadas_com_hash_do_canal', count(distinct seller.id)
  from public.seller_organization seller
  join public.webhook_endpoint endpoint on endpoint.seller_organization_id=seller.id and endpoint.status='active'
  join public.checkout_integration integration
    on integration.seller_organization_id=seller.id and integration.id=endpoint.checkout_integration_id
   and integration.status='active'
  join canal on canal.endpoint_key_sha256=endpoint.endpoint_key_sha256
  where seller.status='active' and seller.financial_operator_id is not null
    and seller.financial_product_catalog_item_id is not null;
-- o predicado do _BACKFILL da 0062, pela chave primária do mapping
with canal as (
  select distinct endpoint.endpoint_key_sha256, endpoint.secret_reference
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  where integration.adapter='generic_hmac_v1' and integration.display_name='Financial checkout'
    and integration.status='active' and endpoint.status='active')
select 'linhas_que_o_backfill_grava', count(*)
  from public.seller_organization seller
  join public.webhook_endpoint endpoint on endpoint.seller_organization_id=seller.id and endpoint.status='active'
  join public.checkout_integration integration
    on integration.seller_organization_id=seller.id and integration.id=endpoint.checkout_integration_id
   and integration.status='active'
  join canal on canal.endpoint_key_sha256=endpoint.endpoint_key_sha256
  join public.catalog_product product on product.seller_organization_id=seller.id
  where seller.status='active' and seller.financial_operator_id is not null
    and seller.financial_product_catalog_item_id is not null
    and not exists (select 1 from public.external_product_mapping mapping
      where mapping.seller_organization_id=seller.id
        and mapping.checkout_integration_id=integration.id
        and mapping.external_product_id_sha256=encode(digest(convert_to(product.id::text,'UTF8'),'sha256'),'hex'));
select 'mappings_total', count(*) from public.external_product_mapping;
select 'dead_letter_sem_mapping', count(*) from app_private.fulfillment_job
  where status='dead_letter' and error_code='product_mapping_missing';
select 'fora_do_dono', count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='app_private' and pg_get_userbyid(p.proowner)<>'appsell_owner';
select 'reconciler', count(*) from pg_roles where rolname='appsell_reconciler';
select 'dono_payout', pg_get_userbyid(relowner) from pg_class
  where oid='public.seller_payout_account'::regclass;
-- o critério de appsell_api.preflight_donos (#509): fora do appsell_owner, sem
-- membros de extensão
with esquema as (select oid from pg_namespace where nspname in ('public','app_private')),
de_extensao as (select classid, objid from pg_depend where deptype='e'),
fora as (
  select 'relação' tipo, n.nspname||'.'||c.relname nome from pg_class c
    join pg_namespace n on n.oid=c.relnamespace
    where c.relnamespace in (select oid from esquema) and c.relkind in ('r','p','v','m','S','f')
      and pg_get_userbyid(c.relowner)<>'appsell_owner'
      and not exists (select 1 from de_extensao d where d.classid='pg_class'::regclass and d.objid=c.oid)
  union all
  select 'função', p.oid::regprocedure::text from pg_proc p
    where p.pronamespace in (select oid from esquema) and pg_get_userbyid(p.proowner)<>'appsell_owner'
      and not exists (select 1 from de_extensao d where d.classid='pg_proc'::regclass and d.objid=p.oid)
  union all
  select 'tipo', t.oid::regtype::text from pg_type t left join pg_class r on r.oid=t.typrelid
    where t.typnamespace in (select oid from esquema) and t.typtype in ('c','d','e','r','m')
      and (t.typtype<>'c' or r.relkind='c') and pg_get_userbyid(t.typowner)<>'appsell_owner'
      and not exists (select 1 from de_extensao d where d.classid='pg_type'::regclass and d.objid=t.oid))
select 'pre_voo_donos', count(*), coalesce(string_agg(tipo||' '||nome, ', ' order by nome), '') from fora;
rollback;
```

## Anexo B — `anexo-B.sql` (conferência, sha256 `ed9010368feb37102021df0797d068175117aa2c4b84614c1c3ef2790c7abee4`)

```sql
begin transaction read only;
select 'versao', version_num from alembic_version;
set local role appsell_runtime;
select 'schema_ready', app_private.schema_ready();
select 'canal_pronto', app_private.financial_channel_ready();
reset role;
select 'fora_do_dono', count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='app_private' and pg_get_userbyid(p.proowner)<>'appsell_owner';
select 'trigger', tgenabled::text from pg_trigger
  where tgrelid='public.catalog_product'::regclass and tgname='catalog_product_financial_mapping';
select 'dono_payout', pg_get_userbyid(relowner) from pg_class
  where oid='public.seller_payout_account'::regclass;
-- o SELECT que as funções de payout fazem, como o dono delas (#509)
set local role appsell_owner;
select 'owner_le_payout', count(*) >= 0 from public.seller_payout_account;
reset role;
rollback;
```
