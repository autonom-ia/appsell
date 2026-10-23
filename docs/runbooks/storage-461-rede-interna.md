# Controle do storage pela rede interna (#461, pré-requisito do #477)

**Nada aqui foi aplicado.** Cada etapa tem o seu OK do Rodrigo, nesta ordem, e
nenhuma começa sem a anterior conferida:

| OK | Etapa | Escreve no host? |
|---|---|---|
| 1 | merge do PR desta flag | não |
| 2 | deploy da imagem com a flag **desligada** | sim (imagem) |
| 3 | leitura do EasyPanel e das redes | não |
| 4 | rede `sell-storage-internal` e alias `appsell-storage` | sim (spec) |
| 5 | ensaio assinado, com o endpoint trocado só no processo | não |
| 5b | merge do #481: o `ensure-asset-worker.sh` põe o worker na rede nova | não |
| 6 | env da api e do worker: flag ligada e endpoint interno | sim (env) |
| 7 | reaplicar a borda do #477 | sim (Traefik) |

## Por que

Lido na produção em 23/10/2026 (#461):

- `APPSELL_API_STORAGE_INTERNAL_ENDPOINT` da `api` e do `asset-worker` aponta
  para `minio.autonomia.site`. O controle do storage (HEAD, multipart, cópia,
  remoção) sai pelo host público e volta pelo Traefik. A borda do #477 fechou
  esse caminho e o `/health/ready` foi a 503, e a borda foi revertida em 38 s.
- O hairpin não era descuido: fora de local/CI o `config.py` exigia https nos
  dois endpoints. A flag `APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED` (este PR)
  aceita http só no endpoint interno e só para um nome de serviço de um
  rótulo.
- `sell-autonomia_storage` resolve dentro dos dois contêineres, mas o MinIO
  responde **400 `InvalidRequest`** a esse Host, porque `_` não é hostname
  válido. Pelo IP da VIP, ou com `Host: appsell-storage:9000`, responde 200.
  Daí o alias sem `_`.

**Por que uma rede só para isso.** Em http, quem está na mesma rede lê os
objetos e pode repetir um pedido assinado dentro da validade. O SigV4 não manda
a secret pela rede; manda o access key id e a assinatura. A rede
`sell-storage-internal` é `--internal` e tem só três serviços: `storage`,
`api` e `asset-worker`. O alias `appsell-storage` existe só nela. **Nunca** na
rede compartilhada do EasyPanel, que outros projetos da VPS enxergam.

**Por que sem `--opt encrypted`, por enquanto.** O IPsec do overlay cifra só o
tráfego **entre nós** do Swarm. A produção é uma VPS só; a etapa 3 confirma
que o Swarm tem um nó. Com um nó, o tráfego da rede nunca sai do host, e a
cifra não protegeria nada. Se a etapa 3 mostrar mais de um nó, parar: a rede
só nasce com `--opt encrypted`, depois de ensaiada. Ligá-la sem ensaio arrisca a própria criação da rede (módulos do
kernel, portas ESP) e não temos prova de que funciona e é gratuita aqui.
**Gatilho de revisão:** no dia em que o Swarm ganhar um segundo nó,
`--opt encrypted` passa a ser obrigatório nesta rede, antes do nó entrar.

**Path-style é obrigatório.** Com a flag ligada e endpoint http, o `config.py`
recusa `APPSELL_API_STORAGE_PATH_STYLE=false`: em virtual-host o cliente
montaria `<bucket>.appsell-storage`, que não resolve. O ensaio da etapa 5
imprime o valor.

## Regras desta janela

- Todo `docker` no host vai com `sudo -n`. Primeira linha de cada sessão:

  ```sh
  sudo -n true || { echo "PARAR: sudo -n não liberado neste host"; exit 1; }
  ```

- Nunca imprimir env. Quando um valor importa, imprime-se só o hostname.
- Nunca usar `updateEnv` nem outro endpoint TRPC do EasyPanel. Env muda com
  `docker service update --env-add`, que não apaga as outras variáveis, e só
  se a etapa 3 mostrar que o EasyPanel não gerencia aquele serviço.
- Backup de spec em `/tmp/<service>_spec_pre461_<timestamp>.json`, com
  `umask 077`, feito pelo `storage-rede-interna.sh` junto da digital por campo
  (`.digital`, só hashes). A spec do `storage` leva a env com a credencial root do MinIO:
  o arquivo fica 600 e é apagado pelo nome exato 24 h depois da etapa 7.

## Etapa 2 — imagem com a flag desligada

Pelo processo de release em vigor (modelo: `release-437-correcoes-rce.md`), na
ordem que o `PRODUCTION_CURRENT.md` manda. Nenhuma env muda. Com a flag
ausente, a validação é a mesma de antes, e a prova disso são os testes de
`test_config_storage_internal_http.py`.

**Pronto quando:** `/health/ready` da api com `"storage":true`, os serviços
com a contagem de env igual à do backup e o smoke da Hub2You igual ao de antes.

## Etapa 3 — leitura do EasyPanel e das redes (só leitura)

Decide **como** a rede e o alias entram. Se o EasyPanel gerencia o serviço e
reescreve a spec num redeploy, um `docker service update` some no próximo
deploy. É a mesma lição de prod-config-não-é-código. Lê só rótulos, redes,
ordem de update e tipo de mount, sem env:

```sh
for s in sell-autonomia_storage sell-autonomia_api sell-autonomia_asset-worker; do
  echo "== $s"
  sudo -n docker service inspect "$s" --format '{{range $k, $v := .Spec.Labels}}{{println $k}}{{end}}'
  sudo -n docker service inspect "$s" --format \
    'update={{.Spec.UpdateConfig.Order}} rollback={{.Spec.RollbackConfig.Order}} replicas={{.Spec.Mode.Replicated.Replicas}} mounts={{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{.Type}}:{{.Target}} {{end}}'
  sudo -n docker service inspect "$s" --format '{{range .Spec.TaskTemplate.Networks}}{{.Target}} {{json .Aliases}}{{println}}{{end}}' \
    | while read -r alvo aliases; do
        printf '%s aliases=%s\n' "$(sudo -n docker network inspect "$alvo" --format '{{.Name}} driver={{.Driver}} internal={{.Internal}} scope={{.Scope}}')" "$aliases"
      done
done
sudo -n docker network ls --format '{{.Name}}' | grep -Fx sell-storage-internal || echo "sell-storage-internal: não existe"
sudo -n docker node ls --format '{{.Hostname}} {{.Status}} {{.Availability}} {{.ManagerStatus}}'   # esperado: 1 linha
```

E no painel do EasyPanel, só olhando: o `storage` é um serviço do projeto
`sell-autonomia`? O painel tem campo de rede ou alias para ele? A `api` tem a
env editada pelo painel?

**Decisão, antes da etapa 4:**

- Se o `storage` e a `api` **não** são gerenciados pelo EasyPanel (sem rótulo
  dele, criados por script): seguir com os comandos abaixo.
- Se **são**: parar. A rede, o alias e a env precisam entrar pela
  configuração que o EasyPanel guarda. O método volta ao Rodrigo antes de
  qualquer escrita.
- O `asset-worker` é criado por `deploy/easypanel-native/ensure-asset-worker.sh`.
  O #481 faz o script exigir `sell-storage-internal` e pôr o worker nela, além
  da rede do ClamAV. O merge dele é a etapa 5b, antes da 6. Sem ele, um worker
  recriado pelo script volta sem a rede e quebra. O script só roda quando o
  worker é recriado, então o merge basta e não há deploy no host.
- A `api` não tem script nosso. Ela é App Service do EasyPanel
  (`deploy/easypanel-native/README.md:21` e `:69`), e os releases só trocam a
  imagem (`docs/PRODUCTION_CURRENT.md:326`), o que preserva as redes e a env. Um
  deploy pelo próprio EasyPanel remontaria a spec e tiraria a rede. É a decisão
  logo acima: se o painel gerencia a api, a rede e a env entram por ele.
- `storage` precisa estar com `update=stop-first` e `rollback=stop-first`:
  MinIO de instância única não pode ter duas tasks no mesmo volume. A etapa 4
  fixa isso no mesmo update. Ler o valor de hoje é só para registrar.

## Etapa 4 — rede e alias

### Antes: worker ocioso, smoke e linha de base

Nada de envio em andamento: a pausa do storage derrubaria um multipart ou um
trabalho de verificação no meio. A consulta, só contagens, numa transação
READ ONLY:

```sh
sudo -n docker exec -i "$(sudo -n docker ps -q --filter name=sell-autonomia_postgres-native-90. | head -1)" \
  psql -U appsell_native_admin -d appsell_native_90 -At <<'SQL'
begin transaction read only;
select 'upload em voo', count(*) from app_private.asset_upload
  where status in ('pending','uploaded','verifying') and expires_at > now();
select 'verificação na fila', count(*) from app_private.asset_verification_job
  where status in ('queued','leased');
select 'limpeza na fila', count(*) from app_private.asset_storage_cleanup_job
  where status in ('queued','leased');
select 'capa em voo', count(*) from app_private.product_cover_upload
  where status = 'pending';
select 'logo em voo', count(*) from app_private.seller_logo_upload
  where status = 'pending';
select 'e-mail em lease', count(*) from app_private.email_outbox
  where status = 'leased';
rollback;
SQL
```

**Seguir só com tudo em 0.** Um `pending` vencido não conta, e o filtro por
`expires_at` já o tira. Diferente de 0: esperar e repetir.

**Janela:** de baixo tráfego, escolhida pelo Rodrigo, e anunciada como **pausa
da loja**, não só das imagens. Cada update ou rollback da api para a loja
inteira: a api é uma task só, `stop-first`, com `--stop-grace-period 330s`
(`PRODUCTION_CURRENT.md`, "O período de graça é parte do contrato"). Com um
e-mail em lease, a parada pode passar de 5 min. A api reinicia na etapa 4, na
6 e em cada rollback. A pausa do `storage` atinge `minio.autonomia.site` e,
com ele, as imagens da vitrine.

**Outros clientes do MinIO do Sell.** O backup do banco
(`deploy/easypanel-native/backup/`, `APPSELL_BACKUP_S3_ENDPOINT`), o
`view-storage` e qualquer `mc` dependem do storage de pé. Antes de marcar a
janela, ler quando o backup roda, sem abrir conteúdo:

```sh
sudo -n ls -l /etc/cron.d/appsell-native-backup 2>&1
sudo -n awk '!/^#/ && NF {print $1, $2, $3, $4, $5}' /etc/cron.d/appsell-native-backup 2>&1
```

e, no painel do EasyPanel, só olhando, se existe serviço agendado de backup
(opção B do `BACKUP_RESTORE_SELL.md`). A janela fica a pelo menos 1 h de
qualquer execução encontrada.

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
      "R$ 47,00", "R$ 47,00" in vitrine, "R$ 87,00", "R$ 87,00" in vitrine,
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

Esperado: vitrine 200, 2 cards, os dois preços, 0 imagens com erro. A
contagem é do texto visível (`>Ver e comprar<`): o payload da página repete a
frase, e a contagem solta dá 4. Cada
página 200, `comprar True`, `href True`, `login 307`, `return_to True`,
`new_user True`, `identity 200`. Antes de qualquer escrita, guardar a saída
como linha de base.

**Medir o tempo real sem storage**, numa segunda sessão local aberta antes do
update, com fim ao Ctrl-C:

```sh
while :; do
  printf '%s storage=%s loja=%s\n' "$(date -u +%T)" \
    "$(curl -s -o /dev/null -m 2 -w '%{http_code}' https://minio.autonomia.site/minio/health/live)" \
    "$(curl -s -o /dev/null -m 5 -w '%{http_code}' https://sell.autonomia.site/s/hub2you/p/condicoes-gerais)"
  sleep 1
done | tee "$TMPDIR/storage-461-pausa.log"
```

O tempo sem storage e o tempo sem loja vão, cada um, da primeira linha
diferente de 200 até a primeira 200 de volta, e entram no registro do #461.

### Escrita

Pelo `deploy/easypanel-native/storage-rede-interna.sh`, copiado para o host
a partir do `main` do merge, com o `sha256sum` conferido contra o do
repositório. Primeiro o modo seco, que só lê:

```sh
bash storage-rede-interna.sh seco
bash storage-rede-interna.sh rede
```

O que o `rede` faz, nesta ordem, e onde para:

1. `sudo -n`, Swarm com 1 nó (com 2, para: a rede teria de nascer com
   `--opt encrypted`, ensaiada antes), os três serviços com 1 task `Running`.
2. Backup das três specs em `/tmp/<serviço>_spec_pre461_<ts>.json`, `umask
   077`, e a digital por campo (`Labels`, imagem, env, mounts, redes,
   recursos, ordem de update e de rollback) em `.digital`, só hashes. O `ts`
   sai impresso: é o nome dos backups.
3. Cria `sell-storage-internal` se não existe e exige `overlay true swarm`.
   Rede existente que não seja interna: para **antes de qualquer update**.
4. `storage` com `--update-order stop-first --rollback-order stop-first
   --network-add name=sell-storage-internal,alias=appsell-storage`, depois a
   api precisa voltar a ver o storage (`probe_availability()` de dentro dela).
5. api, depois worker, com `--network-add sell-storage-internal`; depois de
   cada um, `probe_availability()` de dentro do próprio serviço.

Cada update é conferido: saída do `docker service update`, 1 task `Running`
e a checagem verde em até 180 s, e a contagem de env igual à de antes. Falhou
qualquer um: o script volta os serviços que já tocou, do último para o
primeiro, e sai com 1. Os que ele não tocou ficam como estão. Serviço que já
está na rede é pulado, então rodar de novo depois de uma falha corrigida não
repete o que fechou.

### Conferência

```sh
for s in sell-autonomia_storage sell-autonomia_api sell-autonomia_asset-worker; do
  echo "$s env=$(sudo -n docker service inspect "$s" --format '{{len .Spec.TaskTemplate.ContainerSpec.Env}}') $(sudo -n docker service ps "$s" --filter desired-state=running --format '{{.CurrentState}}' | head -1)"
done
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  cid=$(sudo -n docker inspect --format '{{.Status.ContainerStatus.ContainerID}}' \
    "$(sudo -n docker service ps -q "$s" --filter desired-state=running | head -1)")
  sudo -n docker exec "$cid" python -c '
import socket, urllib.request, urllib.error
print("appsell-storage resolve:", bool(socket.getaddrinfo("appsell-storage", 9000)))
try:
    print("health/live HTTP", urllib.request.urlopen("http://appsell-storage:9000/minio/health/live", timeout=5).status)
except urllib.error.HTTPError as e:
    print("health/live HTTP", e.code)' | sed "s/^/$s /"
done
```

Esperado: a contagem de env de cada serviço **igual** à do backup, os três
`Running`, `resolve: True` e `HTTP 200` nos dois. Depois disso, o passo 7 do
runbook do #477 (probe direto e três leituras do `/health/ready`, todas com
`"storage":true`) e o smoke da Hub2You igual à linha de base.

### Rollback da etapa 4 (≈ 1–2 min)

Com o `ts` impresso pela escrita:

```sh
bash storage-rede-interna.sh desfazer <ts>
```

Worker, api e storage, nesta ordem. Para cada um, compara a digital da spec
atual com a do backup, campo a campo:

- igual ao backup: nada a desfazer (é o que acontece num serviço que o update
  não chegou a mudar, e na segunda execução do mesmo `desfazer`);
- diferente, e a `PreviousSpec` do Swarm igual ao backup: `docker service
  rollback`, e confere de novo que a spec ficou igual ao backup e a task
  `Running`;
- diferente, e a `PreviousSpec` também diferente (um deploy do EasyPanel ou
  outro update entrou no meio): **ABORT, sem rollback**. Um rollback ali cairia
  na spec desse outro deploy. Vai ao Rodrigo, com os campos que diferem.

Desfeitos os três, a rede fica vazia; apagar só ela, pelo nome:

```sh
sudo -n docker network rm sell-storage-internal
```

Depois da etapa 6, o `desfazer` da etapa 4 aborta por desenho: a
`PreviousSpec` passou a ser a da etapa 6. A ordem é desfazer a 6 primeiro e,
se a 4 também tiver de sair, levar ao Rodrigo.

**Aborto automático da etapa 4:** o script já volta sozinho quando um serviço
não fica `Running` em 180 s, a contagem de env muda ou o storage não responde.
Smoke diferente da linha de base, ou a conferência acima falhando: `desfazer
<ts>` sem novo OK, e alerta à Orquestração.

## Etapa 5 — ensaio assinado (só leitura)

O `health/live` não usa assinatura. Este passo prova o SigV4, o path-style e
as credenciais pelo endpoint novo antes de mudar a env. O endpoint é trocado
**só no processo** do ensaio. A credencial já está no contêiner e não é
impressa: a saída é `ok` ou o nome e o código do erro.

As `Settings` são construídas de novo, com a env real do contêiner e os dois
valores da etapa 6 por cima. Assim a validação do `config.py` roda inteira
antes da troca: se ela recusar a env de produção com a flag ligada, o erro
aparece aqui, e não num crash-loop da api `stop-first` depois da etapa 6.
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
        storage_internal_endpoint="http://appsell-storage:9000",
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
as três linhas `ok`. Os mesmos parâmetros do `assert_runtime_controls` que a
api usa no `/health/ready` (`main.py`). Qualquer `erro:` significa parar
aqui. Não há rollback, porque nada mudou.

**Prova negativa do isolamento,** no mesmo passo, só leitura:

```sh
sudo -n docker network inspect --verbose sell-storage-internal \
  --format '{{range $nome, $s := .Services}}{{println $nome}}{{end}}' | sort
#   → exatamente sell-autonomia_api, sell-autonomia_asset-worker e sell-autonomia_storage
sudo -n docker service inspect sell-autonomia_storage \
  --format '{{range .Endpoint.VirtualIPs}}{{.NetworkID}} {{.Addr}}{{println}}{{end}}'
#   → a VIP do storage na rede sell-storage-internal (comparar o NetworkID com o Id da rede)
```

Depois, de dentro da api o nome tem de dar essa VIP (sem a máscara), e de
dentro do `sell-autonomia_web`, que fica só na rede do EasyPanel, não pode
resolver:

```sh
cid_de() { sudo -n docker inspect --format '{{.Status.ContainerStatus.ContainerID}}' \
  "$(sudo -n docker service ps -q "$1" --filter desired-state=running | head -1)"; }
sudo -n docker exec "$(cid_de sell-autonomia_api)" python -c '
import socket; print("api:", sorted({a[4][0] for a in socket.getaddrinfo("appsell-storage", 9000)}))'
sudo -n docker exec "$(cid_de sell-autonomia_web)" node -e '
require("dns").lookup("appsell-storage", (e, a) => { console.log("web:", e ? e.code : a); process.exit(e ? 0 : 1) })'
#   → api: [<a VIP>]   web: ENOTFOUND (rc 0). Se o web resolver, a rede vazou: parar e Rodrigo.
```

## Etapa 6 — env da api e do worker

Só depois da etapa 5 verde, do merge do #481 (etapa 5b) e da decisão da
etapa 3 sobre quem gerencia a env.

```sh
bash storage-rede-interna.sh seco
bash storage-rede-interna.sh env
```

O que o `env` faz e onde para:

1. As mesmas checagens e backups da etapa 4, com um `ts` novo.
2. A rede existe e é `overlay true swarm`, e os três serviços estão nela. Se
   não, a etapa 4 não fechou: para antes de qualquer update.
3. api primeiro, depois worker: `--env-add
   APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true --env-add
   APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://appsell-storage:9000`. Depois
   de cada um: 1 task `Running` e `probe_availability()` verde de dentro dele
   em até 180 s, e a contagem de env igual à de antes **mais as variáveis que
   não existiam** (1 hoje: a flag é nova e o endpoint é substituído). Contagem
   diferente, como uma env que zerou, é falha.
4. Três leituras do `/health/ready` de dentro da api, 15 s entre elas, todas
   com `"storage":true`.

Falhou qualquer passo: volta o que tocou, do último para o primeiro, e sai com
1. Se a api falha, o worker não é tocado.

**Conferência:**

- a contagem de env de cada serviço é a do backup **mais 1** (a flag é nova; o
  endpoint é substituído);
- o comando do item 0 do runbook do #477 imprime `appsell-storage` como
  `INTERNAL_ENDPOINT` nos dois serviços;
- o passo 7 do runbook do #477: probe direto nos dois e três leituras do
  `/health/ready`, 15 s entre elas, todas com `"storage":true`;
- o log do Traefik, nos 5 minutos seguintes, sem `HEAD /appsell-` vindo de
  `172.18.0.1`, porque o controle deixou de passar por ele;
- smoke da Hub2You igual à linha de base.

**Rollback da etapa 6 (≈ 1 min por serviço):** com o `ts` impresso pelo
`env`, o mesmo `desfazer` da etapa 4, com as mesmas três saídas (nada a
desfazer, rollback conferido, ou ABORT e Rodrigo):

```sh
bash storage-rede-interna.sh desfazer <ts>
```

Depois, o comando do item 0 tem de voltar a mostrar `minio.autonomia.site`.

**Aborto automático da etapa 6:** o script já volta sozinho quando a task nova
não fica `Running` em 180 s, o storage não responde, a contagem de env não é
a esperada ou uma das três leituras do `/health/ready` vem sem
`"storage":true`. Smoke diferente da linha de base, ou a conferência acima
falhando: `desfazer <ts>` sem novo OK e alerta à Orquestração.

## Etapa 7 — reaplicar a borda do #477

Pelo `storage-461-borda-traefik.md`. O item 0 dele agora espera
`appsell-storage` como `INTERNAL_ENDPOINT` nos dois serviços. O teste que
importa é o que derrubou a primeira tentativa: depois do `mv`, o
`/health/ready` continua com `"storage":true`, porque o controle já não passa
pela borda.

## Limpeza

24 h depois da etapa 7 estável, apagar pelo nome exato os backups
`/tmp/<serviço>_spec_pre461_<ts>.json` e `.digital` dos dois `ts` anotados.
Atualizar o `PRODUCTION_CURRENT.md` com a rede, o alias, a flag e o tempo de
pausa medido.
