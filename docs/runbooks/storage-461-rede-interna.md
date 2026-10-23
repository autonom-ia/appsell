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
  `umask 077`. A spec do `storage` leva a env com a credencial root do MinIO:
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
rollback;
SQL
```

**Seguir só com tudo em 0.** Um `pending` vencido não conta, e o filtro por
`expires_at` já o tira. Diferente de 0: esperar e repetir.

**Janela:** de baixo tráfego, escolhida pelo Rodrigo. A pausa do `storage`
atinge `minio.autonomia.site` e, com ele, as imagens da vitrine.

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
        j = min(k for k in (texto.find('"', i), texto.find("'", i), texto.find(" ", i)) if k > 0)
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
  printf '%s %s\n' "$(date -u +%T)" \
    "$(curl -s -o /dev/null -m 2 -w '%{http_code}' https://minio.autonomia.site/minio/health/live)"
  sleep 1
done | tee "$TMPDIR/storage-461-pausa.log"
```

O tempo sem storage vai da primeira linha diferente de 200 até a primeira 200
de volta, e entra no registro do #461.

### Escrita

```sh
sudo -n true || { echo "PARAR: sudo -n não liberado neste host"; exit 1; }
umask 077
ts=$(date -u +%Y%m%dT%H%M%SZ)
for s in sell-autonomia_storage sell-autonomia_api sell-autonomia_asset-worker; do
  sudo -n docker service inspect "$s" > "/tmp/${s}_spec_pre461_${ts}.json"
  test -s "/tmp/${s}_spec_pre461_${ts}.json" || { echo "PARAR: backup de $s vazio"; exit 1; }
  echo "$s env=$(sudo -n docker service inspect "$s" --format '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
done
echo "$ts"   # anotar: é o nome dos backups

# 1. a rede, só desta janela
sudo -n docker network create --driver overlay --internal sell-storage-internal
sudo -n docker network inspect sell-storage-internal --format '{{.Driver}} internal={{.Internal}} scope={{.Scope}}'
#    → overlay internal=true swarm

# 2. o storage entra com o alias, sempre stop-first
sudo -n docker service update --detach=false \
  --update-order stop-first --rollback-order stop-first \
  --network-add name=sell-storage-internal,alias=appsell-storage \
  sell-autonomia_storage

# 3. api e worker entram na rede (sem alias). A env não muda aqui.
sudo -n docker service update --detach=false --network-add sell-storage-internal sell-autonomia_api
sudo -n docker service update --detach=false --network-add sell-storage-internal sell-autonomia_asset-worker
```

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

Desfaz na ordem inversa. Nada de env mudou, então não há valor a restaurar:

```sh
sudo -n docker service update --detach=false --network-rm sell-storage-internal sell-autonomia_asset-worker
sudo -n docker service update --detach=false --network-rm sell-storage-internal sell-autonomia_api
sudo -n docker service update --detach=false --update-order stop-first --network-rm sell-storage-internal sell-autonomia_storage
sudo -n docker network rm sell-storage-internal
```

Conferir que as redes de cada serviço voltaram às do backup:

```sh
for s in sell-autonomia_storage sell-autonomia_api sell-autonomia_asset-worker; do
  diff <(python3 -c 'import json,sys; print(sorted(n["Target"] for n in json.load(open(sys.argv[1]))[0]["Spec"]["TaskTemplate"].get("Networks", [])))' "/tmp/${s}_spec_pre461_<ts>.json") \
       <(sudo -n docker service inspect "$s" --format '{{json .Spec.TaskTemplate.Networks}}' | python3 -c 'import json,sys; print(sorted(n["Target"] for n in (json.load(sys.stdin) or [])))') \
    && echo "$s redes iguais ao backup"
done
```

**Aborto automático da etapa 4:** qualquer serviço fora de `Running` em 2 min,
contagem de env diferente do backup, `/health/ready` sem `"storage":true` ou
smoke diferente da linha de base. Rollback sem novo OK, e alerta à Orquestração.

## Etapa 5 — ensaio assinado (só leitura)

O `health/live` não usa assinatura. Este passo prova o SigV4, o path-style e
as credenciais pelo endpoint novo antes de mudar a env. O endpoint é trocado
**só no processo** do ensaio. A credencial já está no contêiner e não é
impressa: a saída é `ok` ou o nome do erro.

```sh
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  cid=$(sudo -n docker inspect --format '{{.Status.ContainerStatus.ContainerID}}' \
    "$(sudo -n docker service ps -q "$s" --filter desired-state=running | head -1)")
  sudo -n docker exec "$cid" python -c '
from appsell_api.config import get_settings
from appsell_api.storage_s3 import S3Storage
atual = get_settings()
ensaio = atual.model_copy(update={"storage_internal_endpoint": "http://appsell-storage:9000"})
storage = S3Storage(ensaio)
print("path_style:", ensaio.storage_path_style)   # tem de ser True
try:
    storage.probe_availability()
    print("HEAD assinado nos dois buckets: ok")
    storage._control.list_objects_v2(Bucket=ensaio.storage_ready_bucket, MaxKeys=1)
    print("GET assinado (listagem de 1 chave): ok")
except Exception as erro:
    print("erro:", type(erro).__name__)' | sed "s/^/$s /"
done
```

Esperado: `path_style: True` e as duas linhas `ok` nos dois serviços. Qualquer `erro:` significa
parar aqui. Não há rollback, porque nada mudou.

## Etapa 6 — env da api e do worker

Só depois da etapa 5 verde, do merge do #481 (etapa 5b) e da decisão da
etapa 3 sobre quem gerencia a env.

```sh
sudo -n true || { echo "PARAR: sudo -n não liberado neste host"; exit 1; }
umask 077
ts=$(date -u +%Y%m%dT%H%M%SZ)
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  sudo -n docker service inspect "$s" > "/tmp/${s}_spec_pre461env_${ts}.json"
  echo "$s env=$(sudo -n docker service inspect "$s" --format '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
done
# api primeiro: se ela não subir, o worker nem é tocado
for s in sell-autonomia_api sell-autonomia_asset-worker; do
  sudo -n docker service update --detach=false \
    --env-add APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true \
    --env-add APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://appsell-storage:9000 \
    "$s"
  echo "$s env=$(sudo -n docker service inspect "$s" --format '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
done
```

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

**Rollback da etapa 6 (≈ 1 min por serviço):** volta à spec anterior, que é a
de antes do `--env-add`. Worker primeiro, depois a api:

```sh
for s in sell-autonomia_asset-worker sell-autonomia_api; do
  sudo -n docker service rollback --detach=false "$s"
  echo "$s env=$(sudo -n docker service inspect "$s" --format '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"   # = backup
done
```

Depois, o comando do item 0 tem de voltar a mostrar `minio.autonomia.site`.

**Aborto automático da etapa 6:** a task nova sem `Running` em 2 min,
`/health/ready` sem `"storage":true` em qualquer das três leituras, ou
contagem de env diferente de backup + 1. Rollback sem novo OK e alerta à
Orquestração.

## Etapa 7 — reaplicar a borda do #477

Pelo `storage-461-borda-traefik.md`. O item 0 dele agora espera
`appsell-storage` como `INTERNAL_ENDPOINT` nos dois serviços. O teste que
importa é o que derrubou a primeira tentativa: depois do `mv`, o
`/health/ready` continua com `"storage":true`, porque o controle já não passa
pela borda.

## Limpeza

24 h depois da etapa 7 estável, apagar pelo nome exato os backups
`/tmp/*_spec_pre461_<ts>.json` e `/tmp/*_spec_pre461env_<ts>.json` anotados.
Atualizar o `PRODUCTION_CURRENT.md` com a rede, o alias, a flag e o tempo de
pausa medido.
