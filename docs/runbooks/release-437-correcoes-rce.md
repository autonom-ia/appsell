# Plano — subir para produção as correções de segurança do `web` (#437, #449)

> **Estado: plano.** Fase 0 executada em 23/10/2026, só leitura, com OK do
> Rodrigo. Da Fase 1 em diante, nada roda sem novo OK explícito dele.
> Base: [`PRODUCTION_RELEASE_APPSSELL.md`](PRODUCTION_RELEASE_APPSSELL.md).
> Revisão: ressalvas da Orquestração e da Qualidade incorporadas. Nenhuma
> remoção por curinga; toda variável usada no host tem guarda `${VAR:?}`.

## O que a Fase 0 mudou

O plano anterior partia de `PRODUCTION_CURRENT.md` (web e api em `68ffd7b`,
04/09). O host respondeu outra coisa. Leitura completa em
[#437](https://github.com/autonom-ia/appsell/issues/437#issuecomment-6364372588).

| Serviço | Em produção (label `revision`) | ID da imagem | Spec anterior |
|---|---|---|---|
| web | `fa13a78` | `sha256:1b71ec566e8c…` | `web:b387252` |
| api | `409ae23` | `sha256:16545966e6fe…` | `api:fa13a78` |
| asset-worker | `409ae23` | mesma da api | `api:d5e00ec` |
| pdf-sandbox | `d5e00ec` | `sha256:bc88217ab7b5…` | `pdf-sandbox:712e5bb` |

- O `7543df9` (PR #430) **já está no ar**: o web `fa13a78` o contém. As tasks
  foram atualizadas em 16/10 às 21:12 UTC, sem registro.
- O web roda **Next 16.3.3** (lido no contêiner), que ainda tem 3 RCE
  críticas, duas sem autenticação. A correção é a #449 (PR #455): next 16.4.2,
  nodemailer 10.x (versão fixada no PR), sharp ≥0.35.5.
- **O host guarda uma imagem só por serviço.** A anterior do web (`b387252`)
  não existe mais. Sem cuidado, não há para onde voltar.

**Portanto o release é só do `web`**, no SHA de merge do PR #455 na `main`.
API, worker, sandbox e banco não mudam: a #449 não toca `apps/api`, e a api já
roda o código mais novo da main.

## Pré-condições (no repositório, sem tocar o host)

1. O PR #455 está mergeado com OK do Rodrigo. O SHA curto do merge na `main`
   é escrito **por extenso** no comentário do #437 que abre a janela, e é ele
   que se cola nos comandos abaixo onde aparece `SHA=<sha-do-merge>`.
2. **CI no SHA exato:** run do App CI com `conclusion=success` no `SHA`, com
   `Certify web on the integrated merge-group SHA` **executado** (`success`,
   não `skipped`):

   ```sh
   gh run list --repo autonom-ia/appsell --workflow "App CI" --commit "$SHA" \
     --json databaseId,event,conclusion
   gh run view <id> --repo autonom-ia/appsell --json jobs \
     --jq '.jobs[].steps[]|select(.name|startswith("Certify web"))|.conclusion'   # success
   ```

   Hoje a `main` não passa nesse critério. A run de push 34692188049
   (`409ae23`) reprovou com:
   - **16 falhas de pytest.** Nenhuma é de produto, e as três causas estão
     corrigidas no PR #451 (#422): o teste da 0019 descia através da 0060, que é
     forward-only; o env do `test-postgres-foundation.sh` vazava para o
     `test_email_worker` (14 falhas); e o `test_api_onboarding` não esperava o
     `display_name`.
   - **`test_storage_s3_contract`** (abort do multipart). Ele roda contra o
     SeaweedFS do CI, e a produção usa MinIO. Está aberto com o dono de
     storage e é da api, não do web.
   - **`pip-audit`** (58 CVEs) e **`pnpm audit`** (19, fechadas pelo #455).

   Para o release do web, o necessário é o `cert-web` verde. Se a run
   inteira não puder ficar verde por causa de passos da api alheios ao
   release, quem decide aceitar é o Rodrigo, explicitamente.
3. **Delta:** do `SHA` até a `main` no momento do release, nada pode tocar
   `app/`, `lib/`, `package.json`, `pnpm-lock.yaml` ou `deploy/`. Se tocar,
   reavaliar.

   ```sh
   git fetch origin && git diff --stat "$SHA" origin/main -- app lib package.json pnpm-lock.yaml deploy   # vazio
   ```
4. Prova local, se usada: um comando por vez, cada saída lida antes do
   próximo. Nunca encadear com `&&`.

   ```sh
   pnpm install --frozen-lockfile
   pnpm lint
   pnpm typecheck
   pnpm test
   corepack pnpm@11.13.0 --pm-on-fail=ignore audit --prod
   pnpm build
   ```

## Regras de shell no host (valem para todas as fases)

- **Nenhum `rm` com curinga, nenhum `rm` de caminho montado com variável.**
  Toda pasta do release é literal e fica sob `/home/claudete/releases/437/`.
- Toda variável usada no host é definida **no próprio host**, na mesma sessão,
  com valor literal, e protegida na primeira linha que a usa:
  `: "${SHA:?SHA vazio — parar}"`. Uma variável definida na máquina local não
  atravessa o `ssh`.
- `rm` só em caminho exato, depois de `test -d <caminho>` e de
  `lsof +D <caminho>` sem saída.
- **Proibidos** até a Fase 3 fechar: `docker image prune`, `docker system
  prune`, `docker volume prune`, `docker rmi` e `docker builder prune` com
  `--all`.

## Fase 0 bis — reconferir o host no dia 🔴 (só leitura, OK do Rodrigo)

Repetir a leitura logo antes da Fase 1. Nunca dar `cat` em spec nem em env:
só campos com `--format`.

```sh
dig +short sell.autonomia.site                 # tem de dar 85.31.60.100, o mesmo do alias claudete
ssh claudete
docker node ls --format '{{.Hostname}} {{.Status}} {{.Availability}}'
docker service inspect sell-autonomia_web --format \
  'image={{.Spec.TaskTemplate.ContainerSpec.Image}} replicas={{.Spec.Mode.Replicated.Replicas}} update={{json .Spec.UpdateConfig}} rollback={{json .Spec.RollbackConfig}} hc={{json .Spec.TaskTemplate.ContainerSpec.Healthcheck}} previous={{if .PreviousSpec}}{{.PreviousSpec.TaskTemplate.ContainerSpec.Image}}{{end}}'
docker image inspect easypanel/sell-autonomia/web:fa13a78 --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker ps --filter name=sell-autonomia_web. --format '{{.ID}}' | head -1 | xargs docker inspect --format '{{.State.Health.Status}}'
# MinIO (storage de produção): health e espaço, só leitura
for s in storage view-storage; do
  docker service ps sell-autonomia_$s --filter desired-state=running --format "$s {{.CurrentState}} {{.Error}}"
  cid=$(docker ps --filter name=sell-autonomia_$s. --format '{{.ID}}' | head -1)
  docker inspect "$cid" --format "$s health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} mounts={{range .Mounts}}{{.Type}}:{{.Source}} {{end}}"
done
# espaço onde moram os volumes do MinIO: df dos Source listados acima
free -h ; df -h / ; uptime
```

**Abort antes de começar** se acontecer qualquer um destes:
- a imagem do web não for `fa13a78` com ID
  `sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86`
  (alguém mexeu; voltar à Orquestração);
- a task do web não estiver `healthy`, ou MinIO sem task `Running`;
- memória `available` abaixo de **4 GiB** (2 GiB do `start-first` mais a folga
  dos outros projetos da VPS);
- disco livre abaixo de 15 GiB no `/` ou no volume do MinIO.

## Smoke — mesmo script antes e depois

Roda na máquina local, não no host. As saídas vão para o scratch da sessão.

```sh
B=https://sell.autonomia.site
O="$(mktemp -d)"
curl -s "$B/s/hub2you" -o "$O/v.html" -w 'vitrine %{http_code}\n'; grep -o 'Ver e comprar' "$O/v.html" | wc -l   # 200 e ≥2
for p in /s/hub2you/p/condicoes-gerais /s/hub2you/p/gerador-de-cotacoes-comparativas; do
  c=$(curl -s -o "$O/p.html" -w '%{http_code}' "$B$p")
  echo "$p $c comprar_agora=$(grep -o 'Comprar agora' "$O/p.html" | wc -l) new_user=$(grep -o 'new_user=true' "$O/p.html" | wc -l)"
done                                                                                   # 200, ≥1, ≥1
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  "$B/auth/login?next=/s/hub2you/p/condicoes-gerais&new_user=true"                    # 307 → auth.autonomia.site/login/ com return_to e new_user=true
curl -s -o /dev/null -w 'identity %{http_code}\n' "<redirect_url acima>"              # 200
for p in / /robots.txt /sitemap.xml /api/health/live /api/health/ready /api/health/startup; do
  curl -s -o /dev/null -w "$p %{http_code}\n" "$B$p"; done                            # 200
for p in /library /auth/signup; do curl -s -o /dev/null -w "$p %{http_code}\n" "$B$p"; done   # 307
```

Linha de base de 23/10, lida antes de qualquer mudança:
- vitrine: 200 com "Ver e comprar" nos 2 produtos (o código da vitrine não tem
  "Comprar agora");
- as duas páginas de produto: 200 com "Comprar agora" e `new_user=true`;
- `/auth/login…`: 307 para o Identity, que responde 200;
- `/library` e `/auth/signup`: 307;
- o resto: 200.

**Gravar a saída de antes no #437. Se o "antes" divergir dessa linha de
base, não fazer release.**

## Fase 1a — preservar o rollback 🔴 (OK do Rodrigo; escreve no host)

Valores literais, sem variável de shell:

```text
ROLLBACK_IMAGE = easypanel/sell-autonomia/web:fa13a78
ROLLBACK_ID    = sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86
ROLLBACK_TAG   = easypanel/sell-autonomia/web:rollback-437-fa13a78
PASTA          = /home/claudete/releases/437
```

Anotar o ID não basta: um ID sem tag pode ser coletado. A primeira escrita no
host dá à imagem uma segunda tag, que nenhum build usa, e guarda os campos da
spec que o rollback precisa (sem env):

```sh
install -d -m 700 /home/claudete/releases/437
docker tag sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86 \
  easypanel/sell-autonomia/web:rollback-437-fa13a78
docker image inspect easypanel/sell-autonomia/web:rollback-437-fa13a78 \
  --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
# esperado: sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86 fa13a78
docker service inspect sell-autonomia_web --format \
  '{"image":{{json .Spec.TaskTemplate.ContainerSpec.Image}},"update":{{json .Spec.UpdateConfig}},"rollback":{{json .Spec.RollbackConfig}},"replicas":{{.Spec.Mode.Replicated.Replicas}}}' \
  > /home/claudete/releases/437/web.spec-campos.antes
docker service inspect sell-autonomia_web --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' \
  | python3 -c 'import json,sys;print("\n".join(sorted(e.split("=",1)[0] for e in json.load(sys.stdin))))' \
  > /home/claudete/releases/437/web.env-names.antes      # só nomes, nunca valores
```

**Abort:** se o `inspect` não devolver esse ID e o label `fa13a78`, parar. O
release não segue sem rollback preservado.

Se um release futuro subir também `api` ou `asset-worker`, o mesmo passo vale
para a imagem deles (`api:409ae23` = `sha256:16545966e6fe…`), com a tag
`rollback-<issue>-409ae23`.

## Fase 1b — construir a imagem 🔴 (OK do Rodrigo)

O host é compartilhado com Manu, Lili e n8n. Um `next build` sem limite pode
levar a VPS a OOM. Por isso a ordem de preferência é:

1. **Preferido: build fora da produção.** A imagem é construída no GitHub
   Actions em `linux/amd64` no SHA exato, sai como `docker save` num artefato
   do run, e chega ao host por `scp` + `docker load`. Hoje o workflow não
   publica o artefato (o `image-web` do App CI usa `push: false` e descarta a
   imagem). Habilitar isso é PR em `.github/`, então não é PR seguro e depende
   do Rodrigo. **Enquanto ele não existir, vale a opção 2.**
2. **No host, com teto e janela registrada.** Builder BuildKit próprio, com
   memória e CPU limitados, fora do horário de pico, com o horário de início
   e fim comentado no #437.

Opção 2, comandos exatos. A fonte vai para um caminho literal:

```sh
# local, worktree limpa exatamente no SHA do merge
: "${TMPDIR:?TMPDIR vazio — parar}"
git -C <worktree> rev-parse --short HEAD          # tem de ser o SHA escrito no #437
git -C <worktree> archive --format=tar.gz -o "$TMPDIR/release-437-src.tar.gz" HEAD
scp "$TMPDIR/release-437-src.tar.gz" claudete:/home/claudete/releases/437/src.tar.gz

# host
SHA=<sha-do-merge>                                 # literal, copiado do #437
: "${SHA:?SHA vazio — parar}"
test ! -e /home/claudete/releases/437/src           # abort se já existir
install -d -m 700 /home/claudete/releases/437/src
tar -xzf /home/claudete/releases/437/src.tar.gz -C /home/claudete/releases/437/src
free -h                                             # abort se available < 6 GiB antes do build
docker buildx create --name release-437 --driver docker-container \
  --driver-opt memory=3g --driver-opt memory-swap=3g --driver-opt cpuset-cpus=0-1
docker buildx build --builder release-437 --load \
  -f /home/claudete/releases/437/src/deploy/easypanel-native/Dockerfile.web \
  --build-arg GIT_SHA="$SHA" -t "easypanel/sell-autonomia/web:$SHA" \
  /home/claudete/releases/437/src
docker buildx rm release-437                        # remove só o builder deste release, pelo nome
```

Conferência da imagem, antes de tocar o serviço:

```sh
: "${SHA:?SHA vazio — parar}"
docker image inspect "easypanel/sell-autonomia/web:$SHA" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'   # = SHA
docker run --rm --entrypoint node "easypanel/sell-autonomia/web:$SHA" -e 'console.log(require("next/package.json").version)'      # 16.4.2
docker run --rm --entrypoint sh "easypanel/sell-autonomia/web:$SHA" -c 'ls node_modules/.pnpm | grep -E "^sharp@"'                  # sharp@0.35.5…
docker run --rm --entrypoint sh "easypanel/sell-autonomia/web:$SHA" -c \
  "grep -rhoaE '\"[0-9]+\.[0-9]+\.[0-9]+\",[A-Za-z_\$]+=\"ECONNECTION\"' .next/server/chunks | sort -u"                             # versão do nodemailer fixada no #455 (vai embutido no bundle)
docker image inspect easypanel/sell-autonomia/web:rollback-437-fa13a78 --format '{{.Id}}'                                          # ainda = ROLLBACK_ID
```

**Safety abort da Fase 1b:**
- o build falhou ou foi morto por memória;
- memória `available` abaixo de 4 GiB em qualquer momento;
- o label difere do SHA;
- next ≠ 16.4.2, sharp < 0.35.5, ou nodemailer diferente da versão fixada no
  #455;
- a imagem de rollback sumiu.

Nesse ponto nada em produção mudou.

## Fase 2 — trocar o `web` 🔴 (OK do Rodrigo)

```sh
: "${SHA:?SHA vazio — parar}"
docker service update \
  --image "easypanel/sell-autonomia/web:$SHA" \
  --update-order start-first \
  --update-failure-action rollback \
  --update-monitor 90s \
  --rollback-order start-first \
  --rollback-failure-action pause \
  sell-autonomia_web
```

- `start-first`: a task nova sobe ao lado da antiga e só a substitui depois de
  `healthy` (o `HEALTHCHECK` da imagem sonda `/api/health/startup`). Não há
  janela de site fora do ar.
- `--update-failure-action rollback` com `--update-monitor 90s`: se a task nova
  falhar em até 90 s, o próprio Swarm volta.
- `--rollback-order start-first`: a volta também não derruba o site. Hoje a
  spec tem `RollbackConfig.Order=stop-first`.
- `--rollback-failure-action pause`: se a volta falhar, o Swarm para e espera,
  em vez de oscilar.

Validação, nesta ordem:

1. `docker service ps sell-autonomia_web --format '{{.Name}} {{.Image}} {{.CurrentState}} {{.Error}}' | head -3`:
   task nova `Running` em `web:$SHA` há ≥ 90 s, health `healthy`, a antiga
   `Shutdown`.
2. `docker service inspect sell-autonomia_web --format '{{.UpdateStatus.State}} previous={{.PreviousSpec.TaskTemplate.ContainerSpec.Image}}'`
   = `completed previous=easypanel/sell-autonomia/web:fa13a78`.
3. Variáveis por nome:

   ```sh
   docker service inspect sell-autonomia_web --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' \
     | python3 -c 'import json,sys;print("\n".join(sorted(e.split("=",1)[0] for e in json.load(sys.stdin))))' \
     > /home/claudete/releases/437/web.env-names.depois
   diff /home/claudete/releases/437/web.env-names.antes /home/claudete/releases/437/web.env-names.depois && echo VARS_IGUAIS
   ```

4. Smoke completo, idêntico à linha de base.
5. `docker service logs --since 5m sell-autonomia_web 2>&1 | grep -Eic 'error|unhandled|fatal'`
   = 0 linhas novas.
6. Prova do bundle: o contêiner em execução responde `16.4.2` a
   `require("next/package.json").version` (`docker exec <cid> node -e …`, só
   leitura).

**Safety abort, com rollback imediato e sem novo OK** (regra 3 do Rodrigo):
- `UpdateStatus` = `rollback_*`;
- task sem convergir em 3 min;
- `VARS_IGUAIS` ausente;
- qualquer linha do smoke diferente da linha de base;
- `/api/health/ready` ≠ 200.

## Rollback (ETA ≈ 1–2 min, sem janela fora do ar)

> ⚠️ **O rollback volta para `fa13a78`, que tem as 3 RCE críticas do Next
> 16.3.3** (GHSA-2464-fhr4-w9xw, GHSA-673q-mjm6-r5xr, GHSA-vcvr-r3jv-pc5j). Ele
> troca um defeito visível por uma exposição conhecida e só vale como estado
> provisório.
>
> **Mitigação enquanto durar:**
> - O código do Sell não usa `next/image`, `next/og`/`ImageResponse` nem
>   `"use cache"` (conferido em `app/` e `lib/` no `d2f67fe`). Mas o endpoint
>   `/_next/image` existe por padrão no Next.
> - A mitigação é bloquear `/_next/image` no Traefik da VPS. O Sell não serve
>   nada por ele, então o impacto é zero. Isso é mudança de infra e depende do
>   OK do Rodrigo; o comando é escrito no #437 antes da janela, junto com o
>   rollback dele.
> - Não há kill-switch de runtime no Next para o `use cache` nem para o
>   `next/og`. A proteção ali é o código não usar esses recursos.
>
> **Prazo para refazer:** a causa do rollback é corrigida e um release novo
> sobe em até **24 h**. Se isso não for possível, o bloqueio do Traefik fica
> ativo até o release.

Antes de confiar no `service rollback`, conferir para onde ele volta:

```sh
docker service inspect sell-autonomia_web --format '{{.PreviousSpec.TaskTemplate.ContainerSpec.Image}}'
# esperado: easypanel/sell-autonomia/web:fa13a78 — o mesmo de /home/claudete/releases/437/web.spec-campos.antes
```

Se bater:

```sh
docker service rollback sell-autonomia_web
```

Se não bater, usar a tag preservada na Fase 1a:

```sh
docker service update --image easypanel/sell-autonomia/web:rollback-437-fa13a78 \
  --update-order start-first --update-failure-action pause sell-autonomia_web
```

`pause` aqui é de propósito: se o próprio rollback falhar, o Swarm não pode
"voltar" para a versão nova que acabou de ser recusada.

Depois: repetir o smoke e registrar sintoma, horário e saída no #437 e em
`PRODUCTION_CURRENT.md`. O banco não entra no rollback, porque o release não
tem migration.

## Fase 3 — fechar

- `PRODUCTION_CURRENT.md` recebe o bloco de evidência do runbook, com as
  imagens **lidas do host** (web, api, worker e sandbox) e a correção do
  registro de 04/09 → 12/09 → 16/10.
- Inventário (#438, PR #448) atualizado com os SHAs reais.
- Limpeza da fonte, só em caminhos exatos, depois de conferir que ninguém usa:

  ```sh
  test -d /home/claudete/releases/437/src && lsof +D /home/claudete/releases/437/src   # sem saída
  rm -r -- /home/claudete/releases/437/src
  test -f /home/claudete/releases/437/src.tar.gz && rm -- /home/claudete/releases/437/src.tar.gz
  ```

  No local: `: "${TMPDIR:?}"; test -f "$TMPDIR/release-437-src.tar.gz" && rm -- "$TMPDIR/release-437-src.tar.gz"`.
- Ficam até 24 h estáveis e novo OK do Rodrigo: `/home/claudete/releases/437/`
  (nomes de variáveis e campos da spec), a imagem `fa13a78` e a tag
  `rollback-437-fa13a78`. A remoção delas também é pelo nome exato
  (`docker rmi easypanel/sell-autonomia/web:rollback-437-fa13a78`), nunca por
  prune.
