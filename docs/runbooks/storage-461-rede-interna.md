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
| 6b | env da api, pelo mesmo script | sim (env) | |
| 7 | reaplicar a borda do #477 | sim (Traefik) | |

## Decisões do Rodrigo (24/10/2026, depois da etapa 3)

- **Rede: "Nome que já existe".** O endpoint interno passa a ser
  `http://sell-autonomia-storage:9000`, o alias que o EasyPanel já dá ao
  `storage` na rede `easypanel-sell-autonomia`. Não há rede nova, e o storage
  não para. A etapa 4 sai do plano.
- **Env da api e do worker: "Pelo script".** A primeira decisão era o Rodrigo
  mudar a api no painel. Ela foi trocada depois da leitura de que um Deploy do
  painel pode reescrever a spec da api (ver "Risco: o painel diverge da
  produção"). As duas variáveis entram por `docker service update --env-add`.
  Os valores não são secretos. **Nenhum agente vê valor**: o script só imprime
  nomes, contagens, hashes e hostnames.

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
- a `--update-order start-first` posta na api pelo script (`ordem_do`, #485).
  Agora o script exige que a spec da api já esteja com `start-first` e não
  muda a ordem.

O modo `env` virou dois, `worker` e `api`, com o mesmo caminho e a mesma
conferência.

**O storage não é tocado.** Nenhuma etapa faz update nele: o alias já existe.
Por isso a ordem de update e de rollback do storage não precisa ser lida nem
fixada antes da janela. Essa leitura existia só por causa da etapa 4.

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

## Etapas 6a e 6b — env do worker, depois da api, pelo script

O worker vem primeiro porque não serve a loja. É a primeira prova com um
processo real, e a volta dele não para a vitrine. A api vem depois, com OK
próprio, em outra execução e com outro `ts`.

```sh
bash storage-rede-interna.sh seco
bash storage-rede-interna.sh worker     # 6a
bash storage-rede-interna.sh api        # 6b, depois da 6a conferida
```

O que cada modo faz, e onde para:

1. **Confere, sem escrever nada:**
   - o `sudo -n` e o Swarm com 1 nó;
   - o `storage` e o serviço com 1 task `Running`;
   - a conferência do alias da etapa 5.1: o serviço na rede em que o storage
     tem o alias, e o `getent` dando só a VIP do storage nessa rede;
   - na api, a ordem de update da spec em `start-first`. O script não muda a
     ordem; se ela não for essa, a troca e a volta parariam a loja;
   - o estado de partida lido em 24/10/2026: a flag ainda não existe, e o
     endpoint existe.

   Qualquer falha: ABORT, sem backup e sem update. Se a flag e o endpoint já
   estão nos valores desta janela, não faz nada.
2. **Backup** da spec e da digital em `/tmp/<serviço>_spec_pre461_<ts>.*`, e
   imprime o `ts`. A digital tem um hash por campo (rótulos, imagem, env
   ordenada, mounts, graça, redes, recursos, ordens de update e rollback) e um
   `Resto`, que é a spec inteira sem a env.
3. **Um update só,** com `--env-add APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true
   --env-add APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://sell-autonomia-storage:9000`,
   sem `--update-order`. Vale a ordem da spec: `start-first` na api, então a
   task nova sobe ao lado da antiga e só a substitui depois de `healthy`, sem
   queda da loja. Duas tasks juntas não mandam e-mail em dobro: o claim é por
   lease com fence no banco (`email_worker.py`).
4. **Confere, em até 180 s** (cada `docker exec` com prazo de 60 s):
   - 1 task `Running`, **na imagem do backup**, e o `probe_availability()`
     verde de dentro dela;
   - a digital contra o backup, onde **só a env** pode ter mudado. Qualquer
     outro campo, inclusive o `Resto`, é falha;
   - a contagem de env igual à do backup **mais 1** (a flag entra; o endpoint é
     substituído), e nenhum nome a menos;
   - o valor de todas as outras variáveis igual ao do backup, comparado por
     hash e sem imprimir;
   - a flag e o endpoint com os valores desta janela, e o alias resolvendo só
     para a VIP do storage de dentro da task nova;
   - na api, três leituras do `/health/ready`, com 15 s entre elas, todas com
     `"storage":true`.

   O `/health/ready` lê o estado de um monitor em segundo plano, com janela
   de frescor (`main.py`). As três leituras mostram que esse monitor ficou
   verde. O probe fresco é o do item anterior, feito de dentro da task.

**A volta automática.** Qualquer saída que não seja o fim verde volta o
serviço ao backup e sai com 1 (130 no sinal). Isso vale para conferência que
falha, erro de comando, Ctrl-C, SIGTERM ou queda do SSH. A volta é a mesma do
`desfazer` abaixo.

Um update só com `--env-add` pode, em tese, normalizar outro campo da spec. Se
isso acontecer, a conferência falha e o serviço volta. É o comportamento
pretendido: a janela fecha sem mudança, e o caso vai ao Rodrigo.

**Conferência depois de cada uma:**

- o comando do item 0 do runbook do #477 imprime `sell-autonomia-storage`
  como `INTERNAL_ENDPOINT` no serviço que mudou;
- o smoke da Hub2You fica igual à linha de base, e o laço não mostra linha
  diferente de 200;
- depois da 6b: nos 5 minutos seguintes, o log do Traefik não mostra
  `HEAD /appsell-` vindo de `172.18.0.1`. O controle deixou de passar por ele.

**Rollback (≈ 1–2 min por serviço):**

```sh
bash storage-rede-interna.sh desfazer <ts>
```

O `ts` só é aceito no formato que a etapa imprime (`AAAAMMDDTHHMMSSZ`). Ctrl-C
e queda do SSH não interrompem o `desfazer`. O script compara a spec atual com
o backup e escolhe uma de três saídas:

1. **Igual ao backup:** nada a desfazer. É o que acontece na segunda execução.
2. **Diferente só nas duas variáveis desta janela:** um update que as põe como
   no backup (`--env-rm` da flag, `--env-add` do endpoint anterior). O valor
   vem do arquivo de backup e vai direto para o argumento do `docker`, nunca
   para a tela. O update vai sem `--update-order`, então vale a ordem da spec:
   na api é `start-first`, **sem parar a loja**. Depois, a digital tem de ser
   igual à do backup, sem nenhuma diferença.
3. **Diferente em qualquer outra coisa** (outro campo da spec, ou o valor de
   outra variável): **ABORT, sem tocar no serviço**, e Rodrigo, com os campos
   que diferem.

O script **nunca** roda `docker service rollback`. Ele usa o rollback order da
spec, `stop-first`, e na api a loja para durante a troca: ~22 s de 500 na
etapa 2, e até 330 s com e-mail em lease. Fica como último recurso, fora do
script, só com OK do Rodrigo, anunciado antes e medido com o laço.

Depois da volta, o script confere a spec igual ao backup e 1 task `Running` na
imagem do backup, com o storage respondendo de dentro dela. Se não for assim:
ABORT e Rodrigo.

**Aborto:** a volta do próprio script é automática. Smoke diferente da linha
de base, ou conferência que falha depois do fim verde: `desfazer <ts>` sem
novo OK, e alerta à Orquestração.

**O `ensure-asset-worker.sh` depois da 6b.** A próxima recriação do worker
copia a env da api, e com ela a flag e o endpoint. O script confere que o
alias existe na rede do worker, mas não que ele resolve só para a VIP do
storage: o worker ainda não existe nessa hora. Depois de recriar o worker,
rodar `bash storage-rede-interna.sh seco` e conferir `alias=ok`.

## Risco: o painel diverge da produção

Este risco já existia e não depende desta janela.

A api e o web são App Services do EasyPanel, com fonte GitHub e Dockerfile
(`deploy/easypanel-native/README.md`). Nenhum release passou pelo painel.
Todos trocaram a imagem por fora, com `docker service update --image`
(`inventario-de-deploy.md`, "Como sobe"). A graça de 330 s da api também
entrou assim (`PRODUCTION_CURRENT.md`, "O período de graça é parte do
contrato"). Os volumes do ClamAV e do sandbox de PDF não constam do README,
que fala em "sem mounts".

O painel guarda a configuração de quando o serviço foi criado. Um Deploy pelo
painel reaplicaria essa configuração, e pode:

- trocar a imagem `api:<sha>` por um build de outra branch;
- voltar a graça para 10 s. Aí o Swarm mata o envio de e-mail no meio, e o
  comprador recebe duas vezes;
- tirar os volumes do ClamAV e do sandbox.

Depois das etapas 6a e 6b, ele também tiraria a flag e o endpoint interno. Com
a borda do #477 aplicada, isso derrubaria o `/health/ready`.

Até a Issue abaixo fechar, **ninguém aplica nada da api nem do web pelo painel
do EasyPanel**.

Texto proposto para a Issue (a Orquestração abre):

> **O painel do EasyPanel diverge da produção do Sell (api e web)**
>
> **Problema.** A api e o web são App Services do EasyPanel, com fonte GitHub
> e Dockerfile. Todos os releases trocaram a imagem por fora do painel
> (`docker service update --image`). A graça de 330 s da api, os volumes de
> `/run/appsell-clamav` e `/run/appsell-pdf`, e, depois do #461, a flag
> `APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED` e o endpoint interno
> `http://sell-autonomia-storage:9000` também entraram por `docker service
> update`. A configuração guardada no painel não foi lida. Um Deploy pelo
> painel pode reconstruir a imagem a partir de outra branch, voltar a graça
> para 10 s (e-mail em dobro), tirar os volumes e desfazer a env do #461. Com
> a borda do #477, isso derruba o `/health/ready`.
>
> **Pronto quando:**
> 1. A configuração do painel da api e do web foi lida pelo Rodrigo, sem
>    agente ver valor: fonte, branch, build, env (só nomes), mounts, graça e
>    zero downtime. Cada campo foi comparado com a spec em produção.
> 2. Há uma decisão escrita. Uma opção: o painel passa a ser a fonte, alinhado
>    com a produção. A outra: os serviços saem do controle de deploy do painel,
>    com o motivo registrado.
> 3. `inventario-de-deploy.md` e `PRODUCTION_CURRENT.md` dizem qual é a fonte
>    da verdade da spec.
> 4. Até lá: nenhum Deploy da api ou do web pelo painel. A regra fica escrita
>    no `inventario-de-deploy.md`.
>
> **Risco:** alto (loja Hub2You). **Tipo:** infra. **Ambiente:** produção.
> Refs #461.

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
