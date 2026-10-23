# Plano — subir para produção as correções de segurança do `web` (#437, #449)

> **Estado: plano.** Fase 0 executada em 23/10/2026, só leitura, com OK do
> Rodrigo. Da Fase 1 em diante, nada roda sem novo OK explícito dele.
> Base: [`PRODUCTION_RELEASE_APPSSELL.md`](PRODUCTION_RELEASE_APPSSELL.md).
> Revisão: aprovado com ressalvas pela Orquestração. Esta versão incorpora as
> ressalvas.

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
  nodemailer 10.2.1, sharp ≥0.35.5.
- **O host guarda uma imagem só por serviço.** A anterior do web (`b387252`)
  não existe mais. Sem cuidado, não há para onde voltar.

**Portanto o release é só do `web`**, no SHA de merge do PR #455 na `main`.
API, worker, sandbox e banco não mudam: a #449 não toca `apps/api`, e a api já
roda o código mais novo da main.

## Pré-condições (no repositório, sem tocar o host)

1. O PR #455 está mergeado com OK do Rodrigo. `SHA` = SHA curto do merge na
   `main`.
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

## Fase 0 bis — reconferir o host no dia 🔴 (só leitura, OK do Rodrigo)

Repetir a leitura logo antes da Fase 1. Nunca dar `cat` em spec nem em env:
só campos com `--format`.

```sh
dig +short sell.autonomia.site                 # tem de dar 85.31.60.100, o mesmo do alias claudete
ssh claudete
docker node ls --format '{{.Hostname}} {{.Status}} {{.Availability}}'
docker service inspect sell-autonomia_web --format \
  'image={{.Spec.TaskTemplate.ContainerSpec.Image}} replicas={{.Spec.Mode.Replicated.Replicas}} update={{json .Spec.UpdateConfig}} hc={{json .Spec.TaskTemplate.ContainerSpec.Healthcheck}}'
docker image inspect easypanel/sell-autonomia/web:fa13a78 --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker ps --filter name=sell-autonomia_web. --format '{{.ID}}' | head -1 | xargs docker inspect --format '{{.State.Health.Status}}'
free -h ; df -h /
```

**Abort antes de começar** se acontecer qualquer um destes:
- a imagem do web não for `fa13a78` com ID `sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86` (alguém
  mexeu; voltar à Orquestração);
- a task não estiver `healthy`;
- memória `available` abaixo de 2 GiB (o `start-first` roda duas tasks do web
  ao mesmo tempo);
- disco livre abaixo de 15 GiB.

## Smoke — mesmo script antes e depois

```sh
B=https://sell.autonomia.site
curl -s "$B/s/hub2you" -o /tmp/v.html -w 'vitrine %{http_code}\n'; grep -c 'Ver e comprar' /tmp/v.html   # 200 e 2
for p in /s/hub2you/p/condicoes-gerais /s/hub2you/p/gerador-de-cotacoes-comparativas; do
  c=$(curl -s -o /tmp/p.html -w '%{http_code}' "$B$p")
  echo "$p $c comprar_agora=$(grep -c 'Comprar agora' /tmp/p.html) new_user=$(grep -o 'new_user=true' /tmp/p.html | wc -l)"
done                                                                                   # 200, ≥1, ≥1
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  "$B/auth/login?next=/s/hub2you/p/condicoes-gerais&new_user=true"                    # 307 → auth.autonomia.site/login/ com return_to e new_user=true
curl -s -o /dev/null -w 'identity %{http_code}\n' "<redirect_url acima>"              # 200
for p in / /robots.txt /sitemap.xml /api/health/live /api/health/ready /api/health/startup; do
  curl -s -o /dev/null -w "$p %{http_code}\n" "$B$p"; done                            # 200
for p in /library /auth/signup; do curl -s -o /dev/null -w "$p %{http_code}\n" "$B$p"; done   # 307
```

Linha de base de 23/10, lida antes de qualquer mudança:
- vitrine: 200 com 2 × "Ver e comprar" (o código da vitrine não tem "Comprar
  agora");
- as duas páginas de produto: 200 com "Comprar agora" e `new_user=true` ×4;
- `/auth/login…`: 307 para o Identity, que responde 200;
- `/library` e `/auth/signup`: 307;
- o resto: 200.

**Gravar a saída de antes no #437. Se o "antes" divergir dessa linha de
base, não fazer release.**

## Fase 1a — preservar a imagem de rollback 🔴 (OK do Rodrigo; escreve no host)

O rollback é escrito **aqui, com valores literais**, antes de mexer em
qualquer coisa. Não depende de variável de shell nem de `/tmp`.

```text
ROLLBACK_IMAGE = easypanel/sell-autonomia/web:fa13a78
ROLLBACK_ID    = sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86
ROLLBACK_TAG   = easypanel/sell-autonomia/web:rollback-437-fa13a78
```

Anotar o ID não basta: se a tag `fa13a78` for sobrescrita ou removida, o ID
fica sem nome e pode ser coletado. A primeira escrita no host é dar à imagem
uma segunda tag, que nenhum build usa:

```sh
docker tag sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86 \
  easypanel/sell-autonomia/web:rollback-437-fa13a78
docker image inspect easypanel/sell-autonomia/web:rollback-437-fa13a78 --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
# esperado: sha256:1b71ec566e8c3a5c9e6885960d8691fca092ed117fc50383569641bc63beba86 fa13a78
```

**Abort:** se o `inspect` não devolver esse ID e o label `fa13a78`, parar. O
release não segue sem rollback preservado.

O release toca só o `web`. Se um release futuro subir também `api` ou
`asset-worker`, o mesmo passo vale para a imagem deles
(`api:409ae23` = `sha256:16545966e6fe…`), com a tag `rollback-<issue>-409ae23`.

- **Proibido `docker image prune`, `docker system prune` ou `docker rmi`** no
  host até a Fase 3 fechar.

## Fase 1b — construir 🔴 (OK do Rodrigo)
- Guardar os **nomes** das variáveis do web, nunca os valores, num arquivo do
  host fora de `/tmp`:

  ```sh
  install -d -m 700 /home/claudete/releases/437
  docker service inspect sell-autonomia_web --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' \
    | python3 -c 'import json,sys;print("\n".join(sorted(e.split("=",1)[0] for e in json.load(sys.stdin))))' \
    > /home/claudete/releases/437/web.env-names.antes
  ```

Fonte e imagem:

```sh
# local, worktree limpa no SHA
git archive --format=tar.gz -o "$TMPDIR/appsell-src-$SHA.tar.gz" "$SHA"
scp "$TMPDIR/appsell-src-$SHA.tar.gz" claudete:/home/claudete/
# host
mkdir /home/claudete/appsell-src-$SHA
tar -xzf /home/claudete/appsell-src-$SHA.tar.gz -C /home/claudete/appsell-src-$SHA
cd /home/claudete/appsell-src-$SHA
docker build -f deploy/easypanel-native/Dockerfile.web --build-arg GIT_SHA=$SHA \
  -t easypanel/sell-autonomia/web:$SHA .
docker image inspect easypanel/sell-autonomia/web:$SHA --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'   # = $SHA
docker run --rm --entrypoint node easypanel/sell-autonomia/web:$SHA -e 'console.log(require("next/package.json").version)'      # 16.4.2
docker run --rm --entrypoint sh easypanel/sell-autonomia/web:$SHA -c 'ls node_modules/.pnpm | grep -E "^sharp@"'                  # sharp@0.35.5…
docker run --rm --entrypoint sh easypanel/sell-autonomia/web:$SHA -c \
  "grep -rhoaE '\"[0-9]+\.[0-9]+\.[0-9]+\",[A-Za-z_\$]+=\"ECONNECTION\"' .next/server/chunks | sort -u"                             # \"10.2.1\" (nodemailer vai embutido no bundle)
docker image inspect easypanel/sell-autonomia/web:rollback-437-fa13a78 --format '{{.Id}}'   # ainda = ROLLBACK_ID
```

**Safety abort da Fase 1:** build falhou; label diferente do `SHA`; next ≠
16.4.2; sharp < 0.35.5; nodemailer ≠ 10.2.1; ou a imagem de rollback sumiu.
Nesse ponto nada em produção mudou.

## Fase 2 — trocar o `web` 🔴 (OK do Rodrigo)

```sh
docker service update \
  --image easypanel/sell-autonomia/web:$SHA \
  --update-order start-first \
  --update-failure-action rollback \
  --update-monitor 90s \
  sell-autonomia_web
```

Com `start-first` a task nova sobe ao lado da antiga e só a substitui depois
de `healthy` (o `HEALTHCHECK` da imagem sonda `/api/health/startup`). Com
`--update-failure-action rollback` e `--update-monitor 90s`, se a task nova
falhar em até 90 s, o próprio Swarm volta para a spec anterior. Não há janela
de site fora do ar.

Validação, nesta ordem:

1. `docker service ps sell-autonomia_web --format '{{.Name}} {{.Image}} {{.CurrentState}} {{.Error}}' | head -3`:
   task nova `Running` em `web:$SHA` há ≥ 90 s, health `healthy`, a antiga
   `Shutdown`.
2. `docker service inspect sell-autonomia_web --format '{{.UpdateStatus.State}}'`
   = `completed` (não `rollback_completed`).
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
6. Prova do bundle: o contêiner em execução responde `16.4.2` ao mesmo
   `require("next/package.json").version` (`docker exec <cid> node -e …`, só
   leitura).

**Safety abort, com rollback imediato e sem novo OK** (regra 3 do Rodrigo):
- `UpdateStatus` = `rollback_*`;
- task sem convergir em 3 min;
- `VARS_IGUAIS` ausente;
- qualquer linha do smoke diferente da linha de base;
- `/api/health/ready` ≠ 200.

## Rollback (ETA ≈ 1–2 min, sem janela fora do ar com start-first)

Primeira opção: volta para a `PreviousSpec`, que depois da Fase 2 é
`web:fa13a78`.

```sh
docker service rollback sell-autonomia_web
```

Se a `PreviousSpec` não for a esperada, usar a tag preservada na Fase 1a:

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
- Depois de 24 h estável, com OK do Rodrigo: decidir se `fa13a78` e a tag
  `rollback-437-fa13a78` podem sair do host. Até lá, nada de prune.
- Apagar `$TMPDIR/appsell-src-$SHA.tar.gz` local e `/home/claudete/appsell-src-$SHA*`
  no host. `/home/claudete/releases/437` fica até a Fase 3 fechar.
