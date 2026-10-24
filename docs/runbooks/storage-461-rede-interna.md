# Controle do storage pela rede do projeto (#461, pré-requisito do #477)

**Nada daqui em diante foi aplicado.** Cada etapa precisa do seu OK do Rodrigo,
nesta ordem, e só começa depois da anterior conferida:

| OK | Etapa | Escreve no host? | Estado |
|---|---|---|---|
| 1 | merge do PR da flag (#480) | não | feito |
| 2 | deploy da imagem com a flag **desligada** | sim (imagem) | feito em 24/10/2026, `api:2759090` |
| 3 | leitura do EasyPanel e das redes | não | feito em 24/10/2026 |
| 4 | — | — | **removida**: não há rede nova |
| 5 | ensaio assinado e `getent`, com o endpoint trocado só no processo | não | |
| 5b | merge deste PR: script e `ensure-asset-worker.sh` no alias existente | não | |
| 6a | env do worker, pelo `storage-rede-interna.sh` | sim (env) | |
| 6b | env da api, **pelo Rodrigo no painel** do EasyPanel | sim (env) | |
| 7 | reaplicar a borda do #477 | sim (Traefik) | |

## Decisões do Rodrigo (24/10/2026, depois da etapa 3)

- **Rede: "Nome que já existe".** O endpoint interno passa a ser
  `http://sell-autonomia-storage:9000`, o alias que o EasyPanel já dá ao
  `storage` na rede `easypanel-sell-autonomia`. Não há rede nova, e o storage
  não para. A etapa 4 sai do plano.
- **Env da api: "Você, no painel".** O Rodrigo muda as duas variáveis da api no
  EasyPanel, seguindo o roteiro da etapa 6b. **Nenhum agente vê valor.** A env
  do worker fica conosco: vai por script, com backup e contagem.

## Por que

Lido na produção em 23/10/2026 (#461):

- O `APPSELL_API_STORAGE_INTERNAL_ENDPOINT` da `api` e do `asset-worker` aponta
  para `minio.autonomia.site`. Por isso o controle do storage (HEAD, multipart,
  cópia, remoção) sai pelo host público e volta pelo Traefik. A borda do #477
  fechou esse caminho, o `/health/ready` foi a 503, e a borda foi revertida em
  38 s.
- O hairpin não era descuido. Fora de local/CI, o `config.py` exigia https nos
  dois endpoints. A flag `APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED` (#480)
  aceita http só no endpoint interno, e só para um nome de serviço de um
  rótulo. `sell-autonomia-storage` passa nessa validação
  (`_is_isolated_service_name`).

**Por que esse nome.** Na etapa 3 (24/10/2026) o `storage` estava na rede
`easypanel-sell-autonomia`, com os aliases `sell-autonomia-storage` e
`storage`, e na rede `easypanel`, sem alias. A api está nas duas redes; o
worker só na do projeto.

- **`storage` não serve.** A api também está na rede compartilhada `easypanel`,
  onde 22 serviços de vários projetos da VPS se enxergam. Um nome curto pode
  coincidir com o de outro projeto.
- **`sell-autonomia_storage` também não.** Ele resolve, mas o MinIO responde
  **400 `InvalidRequest`** a esse Host, porque `_` não é válido em hostname.

O `getent` da etapa 5 prova que o nome escolhido resolve só para a VIP do
storage na rede do projeto, nos dois serviços.

**O que muda em exposição.** Com http, quem está na mesma rede pode ler os
objetos em trânsito e repetir um pedido assinado enquanto ele vale. O SigV4
não manda a secret pela rede; manda o access key id e a assinatura. Hoje a
rede do projeto tem 11 serviços, todos do Sell. A rede compartilhada não entra
no caminho: o alias não existe nela. Uma rede só com storage, api e worker
seria mais estreita. O Rodrigo escolheu esta para não pausar o storage.

**Por que sem cifra.** O Swarm tem um nó (etapa 3). Com um nó, o tráfego entre
contêineres nunca sai do host. O script recusa rodar com dois nós.
**Gatilho de revisão:** no dia em que o Swarm ganhar um segundo nó, este
caminho volta à mesa antes de o nó entrar.

**Path-style é obrigatório.** Com a flag ligada e endpoint http, o `config.py`
recusa `APPSELL_API_STORAGE_PATH_STYLE=false`. Em virtual-host o cliente
montaria `<bucket>.sell-autonomia-storage`, e esse nome não resolve. O ensaio
da etapa 5 imprime o valor.

## O que saiu, e o que fica morto

Saiu do `storage-rede-interna.sh`:

- o modo `rede`;
- a criação de `sell-storage-internal`;
- o alias `appsell-storage`;
- o update do `storage`, com a trava que o acompanhava
  (`storage_sem_clientes_do_alias`);
- o modo `env`, que mudava a api por `--env-add`;
- a ordem `start-first` posta na api (`ordem_do`, #485). A api não é mais
  tocada pelo script, só lida.

No lugar entraram três modos:

- `worker`, que muda só o worker;
- `api-antes` e `api-depois`, que só leem e só gravam o backup em `/tmp`.

Saiu do `ensure-asset-worker.sh` o bloco do #481, que exigia
`sell-storage-internal` (exit 1 se a rede faltasse) e punha o worker nela.
Com a rede fora do plano, a próxima recriação do worker falharia nesse ponto.
O worker volta a entrar só na rede do ClamAV, que é a do projeto. Quando o
endpoint interno copiado da api é um nome de serviço (sem ponto), o script
exige que o `storage` tenha esse nome como alias nessa rede. Sem o alias, o
script falha antes do `service create`.

No host não fica nada morto. A etapa 3 confirmou que `sell-storage-internal`
não existe, e nada da etapa 4 foi aplicado. No repositório não sobra código
morto: o que saiu foi apagado, e o histórico do #480, #481, #483 e #485
guarda a versão anterior.

## Regras desta janela

- Todo `docker` no host vai com `sudo -n`. Primeira linha de cada sessão:

  ```sh
  sudo -n true || { echo "PARAR: sudo -n não liberado neste host"; exit 1; }
  ```

- Nunca imprimir env. Quando um valor importa, imprime-se só o hostname, ou um
  sim/não de uma comparação.
- Nenhum agente usa o `updateEnv` nem outro endpoint TRPC do EasyPanel.
- Backup de spec em `/tmp/<serviço>_spec_pre461_<ts>.json`, com `umask 077`,
  feito pelo `storage-rede-interna.sh`, junto da digital por campo
  (`.digital`, só hashes). O backup leva a env com credenciais. O arquivo fica
  com permissão 600 e é apagado pelo nome exato 24 h depois da etapa 7.
- O script é copiado para o host a partir do `main` do merge, com o
  `sha256sum` conferido contra o do repositório, e roda dentro de um `tmux`.

## Etapa 3 — o que a leitura mostrou (24/10/2026)

- O `storage` e a `api` não têm rótulos e são do projeto `sell-autonomia` do
  EasyPanel. A api é App Service com fonte GitHub e `Dockerfile.api`
  (`deploy/easypanel-native/README.md`).
- O `asset-worker` tem os rótulos `com.autonomia.*`. Ele é criado pelo
  `ensure-asset-worker.sh`, que copia a env da api.
- O `storage` está com `update=stop-first` e `rollback=stop-first`.
- A api está com `update=start-first` e `rollback=stop-first`, graça de
  5m30s, e os volumes `/run/appsell-clamav` e `/run/appsell-pdf`.
- O worker está com `update=stop-first` e `rollback=stop-first`, e graça
  de 30 s.
- As redes `easypanel-sell-autonomia` e `easypanel` são overlay, não
  internas, do Swarm e attachable. O Swarm tem 1 nó.

## Etapa 5 — ensaio assinado (só leitura)

### 5.1 O nome resolve só para o storage do Sell

A VIP do storage em cada rede, e o Id da rede do projeto:

```sh
sudo -n docker network inspect easypanel-sell-autonomia --format '{{.Id}}'
sudo -n docker service inspect sell-autonomia_storage \
  --format '{{range .Endpoint.VirtualIPs}}{{.NetworkID}} {{.Addr}}{{println}}{{end}}'
sudo -n docker service inspect sell-autonomia_storage \
  --format '{{range .Spec.TaskTemplate.Networks}}{{.Target}} {{json .Aliases}}{{println}}{{end}}'
```

Esperado:

- a linha cujo `NetworkID` é o Id de `easypanel-sell-autonomia` traz a VIP do
  storage nessa rede;
- o alias `sell-autonomia-storage` aparece **só** na linha dessa rede.

Depois, de dentro da api e do worker:

```sh
cid_de() { sudo -n docker inspect --format '{{.Status.ContainerStatus.ContainerID}}' \
  "$(sudo -n docker service ps -q "$1" --filter desired-state=running | head -1)"; }
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  echo "== $s"; sudo -n docker exec "$(cid_de "$s")" getent hosts sell-autonomia-storage
done
```

Esperado nos dois: **uma linha**, com exatamente a VIP da rede do projeto, sem
a máscara.

- Outro IP, ou mais de uma linha: o nome também responde por outro serviço
  (na api, pela rede `easypanel`). Parar e levar ao Rodrigo.
- Nenhuma linha: o alias não está na rede do serviço. Parar.

O `storage-rede-interna.sh seco` faz a mesma conferência e imprime
`alias=ok` ou `alias=falha` para cada serviço.

### 5.2 SigV4, path-style e credenciais pelo nome novo

O `health/live` não usa assinatura. Este passo prova a assinatura, o
path-style e as credenciais pelo endpoint novo, antes de mudar a env. O
endpoint é trocado **só no processo** do ensaio. A credencial já está no
contêiner e não é impressa: a saída é `ok`, ou o nome e o código do erro.

As `Settings` são construídas de novo, com a env real do contêiner e os dois
valores da etapa 6 por cima. Assim a validação do `config.py` roda inteira
antes da troca. Se ela recusar a env de produção com a flag ligada, o erro
aparece aqui, e não numa task nova que nunca fica `healthy`.
(`model_copy(update=...)` não rodaria os validadores.)

```sh
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  cid=$(sudo -n docker inspect --format '{{.Status.ContainerStatus.ContainerID}}' \
    "$(sudo -n docker service ps -q "$s" --filter desired-state=running | head -1)")
  sudo -n docker exec "$cid" python -c '
from appsell_api.config import Settings
from appsell_api.storage_s3 import S3Storage
def falha(erro):
    codigo = getattr(erro, "response", {}).get("Error", {}).get("Code", "-")
    print("erro:", type(erro).__name__, codigo)
try:
    ensaio = Settings(
        storage_internal_http_allowed=True,
        storage_internal_endpoint="http://sell-autonomia-storage:9000",
    )
    print("validação do config.py: ok, path_style:", ensaio.storage_path_style)
except Exception as erro:
    falha(erro)
    raise SystemExit(1)
storage = S3Storage(ensaio)
try:
    storage.probe_availability()
    print("HEAD assinado nos dois buckets: ok")
    storage.assert_runtime_controls(
        allowed_origin=ensaio.storage_allowed_origin,
        require_tls=True,
        cors_mode=getattr(ensaio, "storage_cors_mode", "s3"),
    )
    print("versionamento e CORS dos dois buckets: ok")
    storage.object_exists(bucket=ensaio.storage_ready_bucket, key="ensaio-461/nao-existe")
    print("HEAD assinado de objeto: ok")
except Exception as erro:
    falha(erro)' | sed "s/^/$s /"
done
```

Esperado nos dois serviços: `validação do config.py: ok, path_style: True` e
as três linhas `ok`. Os parâmetros do `assert_runtime_controls` são os mesmos
que a api usa no `/health/ready` (`main.py`). Qualquer `erro:` manda parar
aqui. Não há rollback, porque nada mudou.

## Etapa 5b — merge deste PR

O `ensure-asset-worker.sh` só roda quando o worker é recriado. O merge basta;
não há deploy no host. Sem este merge, a recriação do worker falha por causa
do bloco do #481.

## Linha de base, antes das etapas 6a e 6b

**Smoke da Hub2You (AGENTS.md §7), na máquina local, antes e depois de cada
escrita.** Sem sessão; só códigos e contagens:

```sh
python3 -I - <<'PY'
import html, urllib.error, urllib.parse, urllib.request
BASE = "https://sell.autonomia.site"
class SemSeguir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None
abrir = urllib.request.build_opener(SemSeguir).open
def get(url):
    try:
        r = abrir(urllib.request.Request(url, headers={"User-Agent": "smoke-461"}), timeout=20)
        return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, ""
def imagens(texto):
    urls, i = set(), 0
    while (i := texto.find("https://minio.autonomia.site/", i)) >= 0:
        j = min((k for k in (texto.find('"', i), texto.find("'", i), texto.find(" ", i)) if k > 0), default=len(texto))
        urls.add(html.unescape(texto[i:j].replace("\\u0026", "&").rstrip("\\"))); i = j
    return [get(u)[0] for u in urls]
cod, _, vitrine = get(BASE + "/s/hub2you")
img = imagens(vitrine)
print("vitrine", cod, "cards", vitrine.count(">Ver e comprar<"),
      "R$ 47,00", "R$ 47,00" in vitrine, "R$ 87,00", "R$ 87,00" in vitrine,
      "imagens", len(img), "com erro", sum(c != 200 for c in img))
for p in ("condicoes-gerais", "gerador-de-cotacoes-comparativas"):
    caminho = f"/s/hub2you/p/{p}"
    cod, _, pagina = get(BASE + caminho)
    href = "/auth/login?next=" + urllib.parse.quote(caminho, safe="") + "&new_user=true"
    login, cab, _ = get(BASE + href)
    destino = cab.get("location", "")
    idp = get(destino)[0] if destino else "-"
    print(p, cod, "comprar", "Comprar agora" in pagina,
          "href", html.escape(href) in pagina or href in pagina,
          "login", login, "return_to", "return_to" in destino, "new_user", "new_user=true" in destino,
          "identity", idp)
PY
```

Esperado:

- vitrine 200, 2 cards, os dois preços e 0 imagens com erro. A contagem é do
  texto visível (`>Ver e comprar<`). O payload da página repete a frase, e a
  contagem solta dá 4.
- cada página: 200, `comprar True`, `href True`, `login 307`,
  `return_to True`, `new_user True` e `identity 200`.

Antes de qualquer escrita, guardar a saída como linha de base.

**Laço de disponibilidade**, numa segunda sessão local aberta antes de cada
escrita, até o Ctrl-C:

```sh
while :; do
  printf '%s storage=%s loja=%s\n' "$(date -u +%T)" \
    "$(curl -s -o /dev/null -m 2 -w '%{http_code}' https://minio.autonomia.site/minio/health/live)" \
    "$(curl -s -o /dev/null -m 5 -w '%{http_code}' https://sell.autonomia.site/s/hub2you/p/condicoes-gerais)"
  sleep 1
done | tee "$TMPDIR/storage-461-janela.log"
```

Qualquer linha diferente de 200 entra no registro do #461, com início e fim.

**Worker ocioso, só para a etapa 6a.** O worker troca com `stop-first`, e um
trabalho de verificação no meio seria interrompido. A consulta, só contagens,
numa transação READ ONLY:

```sh
sudo -n docker exec -i "$(sudo -n docker ps -q --filter name=sell-autonomia_postgres-native-90. | head -1)" \
  psql -U appsell_native_admin -d appsell_native_90 -At <<'SQL'
begin transaction read only;
select 'verificação na fila', count(*) from app_private.asset_verification_job
  where status in ('queued','leased');
select 'limpeza na fila', count(*) from app_private.asset_storage_cleanup_job
  where status in ('queued','leased');
rollback;
SQL
```

Seguir só com as duas contagens em 0. Diferente de 0: esperar e repetir.

## Etapa 6a — env do worker, por script

Vem antes da api porque o worker não serve a loja. É a primeira prova com um
processo real, e o rollback dele não para a vitrine.

```sh
bash storage-rede-interna.sh seco
bash storage-rede-interna.sh worker
```

O que o `worker` faz, e onde para:

1. Confere, sem escrever nada:
   - o `sudo -n` e o Swarm com 1 nó;
   - o `storage` e o worker com 1 task `Running`;
   - o rollback order do worker, que tem de ser `stop-first`;
   - a conferência do alias da etapa 5.1: o worker na rede em que o storage
     tem o alias, e o `getent` dando só a VIP do storage nessa rede.
   Qualquer falha: ABORT, sem backup e sem update.
2. Se a flag e o endpoint já estão nos valores desta janela, não faz nada.
3. Faz o backup da spec e da digital em `/tmp/sell-autonomia_asset-worker_spec_pre461_<ts>.*`
   e imprime o `ts`.
4. Roda um update só, com `--env-add APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true
   --env-add APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://sell-autonomia-storage:9000`.
   A ordem de update fica a da spec.
5. Confere, em até 180 s:
   - 1 task `Running`;
   - o `probe_availability()` verde de dentro do worker;
   - a contagem de env igual à de antes **mais as variáveis que não existiam**
     (1 hoje: a flag é nova e o endpoint é substituído);
   - nenhum nome de variável a menos;
   - a flag e o endpoint com os valores desta janela.

Qualquer saída que não seja o fim verde volta o worker ao backup e sai com 1
(130 no sinal). Isso vale para conferência que falha, erro de comando, Ctrl-C,
SIGTERM ou queda do SSH.

**Conferência:** o comando do item 0 do runbook do #477 imprime
`sell-autonomia-storage` como `INTERNAL_ENDPOINT` no worker. O smoke da
Hub2You fica igual à linha de base.

**Rollback da etapa 6a (≈ 1 min, sem efeito na loja):**

```sh
bash storage-rede-interna.sh desfazer <ts>
```

O `ts` só é aceito no formato que a etapa imprime (`AAAAMMDDTHHMMSSZ`). O
script compara a digital da spec atual com a do backup, campo a campo:

- igual ao backup: nada a desfazer. É o que acontece na segunda execução;
- diferente, e a `PreviousSpec` do Swarm igual ao backup: `docker service
  rollback`, e confere de novo que a spec ficou igual ao backup e a task
  `Running`;
- diferente, e a `PreviousSpec` também diferente: **ABORT, sem rollback**.
  Um rollback ali cairia na spec de outro deploy. Vai ao Rodrigo, com os campos
  que diferem.

**Aborto automático:** o script volta sozinho. Smoke diferente da linha de
base ou conferência que falha: `desfazer <ts>` sem novo OK, e alerta à
Orquestração.

## Etapa 6b — env da api, pelo Rodrigo no painel

A loja depende da api. Nesta etapa nenhum agente escreve na api. O agente só
fotografa a spec antes e confere depois, sem ver valor.

### Trava antes de abrir o editor de env (bloqueante)

A api é App Service do EasyPanel com fonte GitHub e `Dockerfile.api`
(`deploy/easypanel-native/README.md`). Mas nenhum release dela passou pelo
painel. Todos trocaram a imagem por fora, com `docker service update --image`
(`inventario-de-deploy.md`, "Como sobe"). A graça de 330 s também entrou
assim (`PRODUCTION_CURRENT.md`, "O período de graça é parte do contrato"). Os
dois volumes da api não constam do README, que fala em "sem mounts".

**Não foi lido o que um Deploy do painel faz com isso.** Se o painel aplicar a
env reconstruindo a imagem a partir do fonte que ele guarda, ele pode:

- trocar a imagem `api:<sha>` por um build de outra branch;
- voltar a graça para 10 s. Aí o Swarm mata o envio de e-mail no meio, e o
  comprador recebe duas vezes;
- tirar os volumes do ClamAV e do sandbox de PDF.

Antes de editar, o Rodrigo confere no painel, só olhando:

1. **Source** da api: GitHub (repositório, branch, commit) ou imagem Docker?
2. Existe um caminho para aplicar a env **sem build**, só com restart ou
   redeploy da imagem atual?
3. **Mounts**: aparecem os volumes de `/run/appsell-clamav` e
   `/run/appsell-pdf`?
4. Em **Deploy** ou **Advanced**: há campo de graça (stop grace period) e de
   zero downtime? Com que valor?

**Parar e voltar à Orquestração** sem salvar nada se:

- a aplicação da env exige build a partir do GitHub;
- os mounts não aparecem;
- a graça não é configurável no painel.

Qualquer um dos três reescreveria a spec que o `api-depois` exige igual. Com
a imagem atual mantida, os mounts no painel e a graça em 330 s (ou sem que o
painel a toque), seguir.

### Antes

1. Linha de base: smoke da Hub2You e laço de disponibilidade.
2. No host, o agente fotografa a api:

   ```sh
   bash storage-rede-interna.sh api-antes
   ```

   Ele confere o `sudo -n`, o Swarm com 1 nó, as tasks `Running`, o rollback
   `stop-first` e o alias de dentro da api (etapa 5.1). Depois grava
   `/tmp/sell-autonomia_api_spec_pre461_<ts>.{json,digital}` e imprime a
   contagem de env, o hostname do endpoint interno (`minio.autonomia.site`) e
   o `ts`. Não escreve na api.
3. O Rodrigo anota, para ele, o valor **atual** de
   `APPSELL_API_STORAGE_INTERNAL_ENDPOINT`. É o valor do rollback. Nenhum
   agente o vê.

### No painel, nesta ordem

Nomes exatos, sem espaço antes nem depois do `=`:

1. `APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true` (nova).
2. `APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://sell-autonomia-storage:9000`
   (substitui o valor atual; o nome é o mesmo).
3. Nenhuma outra variável muda, sai ou entra. Salvar as duas juntas e aplicar
   uma vez.

A ordem importa se o painel aplicar uma de cada vez. A flag sozinha não muda
nada: com o endpoint https, a validação é a mesma de hoje. O endpoint http
sem a flag é recusado pelo `config.py`, e a task nova nunca fica `healthy`.

O painel está com `zeroDowntime=true`, isto é, `start-first`. A task nova sobe
ao lado da antiga e só a substitui depois de `healthy`, sem queda da loja.
Duas tasks juntas não mandam e-mail em dobro: o claim é por lease com fence no
banco (`email_worker.py`).

### Depois

1. Quando a task nova estiver `Running`, o agente roda:

   ```sh
   bash storage-rede-interna.sh api-depois <ts>
   ```

   Ele só lê, e sai com 1 na primeira falha, dizendo qual rollback usar.
   Confere:

   - a digital por campo contra o backup. Só a env pode ter mudado. Imagem,
     graça, mounts, redes, recursos, rótulos ou ordens de update e rollback
     diferentes saem como `o painel mudou mais que a env: <campos>`;
   - a contagem de env igual à do backup mais 1, e nenhum nome a menos;
   - a flag e o endpoint com os valores desta janela;
   - o `probe_availability()` de dentro da api e o alias resolvendo só para a
     VIP do storage;
   - três leituras do `/health/ready`, com 15 s entre elas, todas com
     `"storage":true`.
2. O item 0 do runbook do #477 imprime `sell-autonomia-storage` como
   `INTERNAL_ENDPOINT` nos dois serviços.
3. O smoke da Hub2You fica igual à linha de base, e o laço sem linha diferente
   de 200.
4. Nos 5 minutos seguintes, o log do Traefik não mostra `HEAD /appsell-` vindo
   de `172.18.0.1`: o controle deixou de passar por ele.

### Rollback da etapa 6b

Há dois rollbacks, conforme o que o `api-depois` disser:

- **Só a env está errada,** ou o storage não responde. O Rodrigo volta pelo
  painel:
  - `APPSELL_API_STORAGE_INTERNAL_ENDPOINT` ao valor anotado no passo 3;
  - `APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED` removida.

  Depois salva e aplica, com a mesma trava e o mesmo `start-first`, e a loja
  não para. ETA: 2 a 3 min. Conferência: o item 0 do #477 volta a mostrar
  `minio.autonomia.site`, o `/health/ready` volta com `"storage":true`, e o
  smoke volta a ser igual à linha de base.
- **O painel mudou mais que a env** (imagem, graça, mounts). O rollback do
  painel não desfaz isso. O agente roda `bash storage-rede-interna.sh
  desfazer <ts>`. Ele usa a `PreviousSpec` do Swarm só se ela for igual ao
  backup; se não for, ABORT e Rodrigo. O rollback da api é `stop-first`, então
  **a loja para durante a troca**: ~22 s de 500 na etapa 2, e até 330 s com
  e-mail em lease. Anunciar antes de executar, e medir com o laço.

  Depois disso, o painel guarda uma config diferente da spec em produção. O
  próximo Deploy pelo painel a reaplicaria. Até o Rodrigo decidir como
  alinhar, ninguém aplica nada da api pelo painel.

**Aborto:** smoke diferente da linha de base, ou `api-depois` com saída 1. Um
desses dois rollbacks, sem novo OK, e alerta à Orquestração.

## Etapa 7 — reaplicar a borda do #477

Pelo `storage-461-borda-traefik.md`. O item 0 dele espera
`sell-autonomia-storage` como `INTERNAL_ENDPOINT` nos dois serviços.

O teste que importa é o que derrubou a primeira tentativa. Depois do `mv`, o
`/health/ready` tem de continuar com `"storage":true`, porque o controle já
não passa pela borda.

## Limpeza

24 h depois da etapa 7 estável:

- apagar pelo nome exato os backups `/tmp/<serviço>_spec_pre461_<ts>.json` e
  `.digital` dos `ts` anotados;
- atualizar o `PRODUCTION_CURRENT.md` com o endpoint interno, a flag e o
  resultado do laço de disponibilidade.
