# Plano — subir as correções de RCE para produção (#437)

> **Estado: plano.** Nada aqui foi executado. Cada fase marcada 🔴 só começa
> com o OK explícito do Rodrigo, inclusive a Fase 0, que é só leitura no host.
> Base: [`PRODUCTION_RELEASE_APPSSELL.md`](PRODUCTION_RELEASE_APPSSELL.md).

## O que está em jogo

`7543df9` (PR #430) sobe `next` 16.2.11 → 16.3.3 e `nodemailer` 9.1.0 → 9.1.1.
As duas dependências são do **`web`** (Next): a API Python não muda para fechar
as RCE. O último estado lido do host (04/09) é `web` e `api` em `68ffd7b`.

Delta `68ffd7b..origin/main` (`409ae23`), lido no repositório em 23/10:

| Serviço | O que muda | Risco |
|---|---|---|
| `web` | Next 16.3.3, nodemailer 9.1.1 (#430); `HEALTHCHECK` sonda `/api/health/startup` (nova rota, #428); link de login do checkout marca usuário novo (#426, toca `/s/<loja>/p/<produto>`) | médio: minor do Next muda render |
| `api` + `asset-worker` | antivírus deixa de ser fatal na subida e passa a ser medido pela base publicada (`clamav_mirror`, consulta DNS TXT a `current.cvd.clamav.net`) (#428, #432, #434); `HEALTHCHECK` sonda `/health/startup` | médio: muda o caminho do incidente de 12/09 |
| `pdf-sandbox` | nada | — fica onde está |
| banco | **nenhuma migration nova**. `0060`/`0061` já estão no host (head `0061`, 04/09); o #425 mexeu só no `downgrade` da `0061` e na formatação da `0059` | baixo |

Nenhuma variável de ambiente nova no delta (procurado em `apps/api/src`, `lib/`,
`app/api/health`).

**Fato que muda a leitura:** `docs/TROUBLESHOOTING.md` registra 3h06 de 502 em
12/09/2026. Pode ter havido deploy ou restart depois de 04/09 sem registro. Por
isso a Fase 0 é obrigatória: o plano parte do que o host responder, não desta
página.

## Decisão pedida ao Rodrigo

| Opção | O quê | Prós | Contras |
|---|---|---|---|
| **A (recomendada)** | Só `web` em `409ae23` | fecha as duas RCE; rollback de uma linha; não toca API, banco nem worker | api fica com o antivírus fatal na subida (o defeito do incidente de 12/09) até a Fase 3 |
| B | `web` e depois `api`+`asset-worker` em `409ae23`, na mesma janela | tudo da `main` no ar de uma vez | dobra a superfície da janela; o caminho do antivírus novo nunca rodou em produção |
| C | Só `web` em `7543df9` exato | menor diff possível | sobe um `web` cuja `HEALTHCHECK` ainda sonda `/ready` (o defeito que o #428 corrigiu) e deixa `b387252` fora; não há ganho real sobre A |

Recomendo **A agora** e **a Fase 3 (api + worker) como janela própria**, depois
de A estável por 24 h. O resto deste plano segue A e descreve a Fase 3 à parte.

## Pré-condições (no repositório, sem tocar o host)

- [ ] CI verde no SHA alvo. Hoje **não existe**: o App CI está desligado (#422).
  Até o #422 fechar, a prova é local e deve ser anexada ao #437:
  `pnpm install --frozen-lockfile && pnpm test && pnpm build` no `409ae23`, com
  a saída lida. Sem isso, o Rodrigo decide se aceita release sem CI.
- [ ] `git rev-parse origin/main` = `409ae23…` no momento do release. Se a `main`
  andou, reavaliar o delta antes de seguir.

## Fase 0 — ler o host 🔴 (OK do Rodrigo, só leitura)

```sh
dig +short sell.autonomia.site            # resolver antes; em 31/08 o alias apontava para máquina desativada
ssh claudete                              # confirmar que o IP do host = o do dig
docker service ls --filter name=sell-autonomia --format '{{.Name}} {{.Image}} {{.Replicas}}'
docker service inspect sell-autonomia_web sell-autonomia_api sell-autonomia_asset-worker \
  --format '{{.Spec.Name}} {{.Spec.TaskTemplate.ContainerSpec.Image}} {{.UpdatedAt}}'
docker service inspect sell-autonomia_api --format '{{.Spec.TaskTemplate.ContainerSpec.StopGracePeriod}}'   # tem que ser 5m30s
docker images 'easypanel/sell-autonomia/*' --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}'
df -h / ; free -g ; uptime                # parar se disco < 15 GiB livre ou memória livre < 2 GiB
```

Saída esperada e decisão:

- `web` já em SHA que contém `7543df9` (`git merge-base --is-ancestor 7543df9 <sha>`):
  **não há release**. Registrar o SHA em `PRODUCTION_CURRENT.md` e fechar o #437.
- `web` em `68ffd7b` ou outro SHA sem `7543df9`: seguir para a Fase 1.
- Algo inesperado (serviço a mais, réplica 0/1, imagem sem tag de SHA): parar e
  trazer à Orquestração.

## Smoke Hub2You — mesmo script antes e depois

Roda de qualquer máquina (é tráfego público, mas conta como leitura de
produção e entra no mesmo OK da Fase 0):

```sh
for p in /s/hub2you /s/hub2you/p/condicoes-gerais /s/hub2you/p/gerador-de-cotacoes-comparativas; do
  body=$(curl -s -o /tmp/smoke.html -w '%{http_code}' "https://sell.autonomia.site$p")
  grep -q 'Comprar agora' /tmp/smoke.html && cta=sim || cta=NAO
  echo "$p $body comprar_agora=$cta"
done
for p in / /auth/login /auth/signup /robots.txt /sitemap.xml /api/health/live /api/health/ready /library; do
  curl -s -o /dev/null -w "$p %{http_code}\n" "https://sell.autonomia.site$p"
done
```

Linha de base exigida: as três da Hub2You = `200 comprar_agora=sim`; `/library`
= `307`; o resto `200`. Gravar a saída de **antes** no #437. Se a linha de base
de antes já falhar, não fazer release: é outro incidente.

## Fase 1 — backup e build 🔴 (OK do Rodrigo)

```sh
SHA=409ae23
TS=$(date +%Y%m%d-%H%M%S)
D=/tmp/sell-release-437-$TS && mkdir -m 700 "$D"
docker service inspect sell-autonomia_web > "$D/web.spec.json"
docker service inspect sell-autonomia_web --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' \
  | python3 -c 'import json,sys;print("\n".join(sorted(e.split("=",1)[0] for e in json.load(sys.stdin))))' > "$D/web.env-names"
docker service inspect sell-autonomia_web --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' > "$D/web.image-anterior"
```

- O `web` não toca banco nesta opção, então o `pg_dump` não é pré-requisito do
  passo; ainda assim, fazer o dump do runbook (`pg_dump -Fc` +
  `pg_restore --list`) se a Fase 3 for na mesma janela.
- Nunca imprimir `Env` com valores. Só nomes.

Fonte e imagem (mesmo caminho dos releases de 02–04/09):

```sh
# na máquina local, worktree limpa em origin/main
git archive --format=tar.gz -o /tmp/appsell-src-$SHA.tar.gz $SHA
scp /tmp/appsell-src-$SHA.tar.gz claudete:/home/claudete/
# no host
mkdir /home/claudete/appsell-src-$SHA && tar -xzf /home/claudete/appsell-src-$SHA.tar.gz -C /home/claudete/appsell-src-$SHA
cd /home/claudete/appsell-src-$SHA
docker build -f deploy/easypanel-native/Dockerfile.web --build-arg GIT_SHA=$SHA \
  -t easypanel/sell-autonomia/web:$SHA .
docker run --rm --entrypoint node easypanel/sell-autonomia/web:$SHA -e 'console.log(require("next/package.json").version)'   # 16.3.3
```

**Safety abort da Fase 1:** build falhou, versão do Next ≠ `16.3.3`, ou
disco/memória abaixo do limite → parar. Nada em produção mudou ainda.

## Fase 2 — trocar o `web` 🔴 (OK do Rodrigo)

```sh
docker service update --image easypanel/sell-autonomia/web:$SHA \
  --update-order stop-first sell-autonomia_web
docker service ps sell-autonomia_web --no-trunc | head -5        # Running em web:$SHA
docker service inspect sell-autonomia_web --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' \
  | python3 -c 'import json,sys;print("\n".join(sorted(e.split("=",1)[0] for e in json.load(sys.stdin))))' > "$D/web.env-names.depois"
diff "$D/web.env-names" "$D/web.env-names.depois" && echo VARS_IGUAIS
```

`stop-first` derruba o `web` por alguns segundos (como nos releases de
02–04/09). Fazer fora do horário de pico.

Validação, nesta ordem:

1. `docker service ps` mostra a task nova `Running` há ≥ 60 s e o health do
   contêiner `healthy` (`docker inspect --format '{{.State.Health.Status}}' <cid>`).
2. `https://sell.autonomia.site/api/health/ready` = 200 com `redis`, `api` e
   `storage_upload` verdadeiros; `/api/health/startup` = 200.
3. Smoke Hub2You idêntico à linha de base.
4. `docker service logs --since 5m sell-autonomia_web 2>&1 | grep -Ei 'error|unhandled|fatal'`
   sem linha nova.
5. Prova do bundle novo (health verde não prova bundle): para visitante
   anônimo, o link de login da página do produto passa a ser gerado por
   `productCheckoutLoginHref` (#426) e leva `new_user=true`. Antes do release a
   contagem é 0; depois, ≥ 1:
   `curl -s https://sell.autonomia.site/s/hub2you/p/condicoes-gerais | grep -c 'new_user=true'`

**Safety abort → rollback imediato, sem novo OK** (regra 3 do Rodrigo): task
não converge em 3 min, `VARS_IGUAIS` não aparece, qualquer linha do smoke
Hub2You diferente da linha de base, ou `ready` ≠ 200.

## Rollback (ETA ≈ 1–2 min)

A imagem anterior continua no host:

```sh
docker service update --image "$(cat $D/web.image-anterior)" \
  --update-order stop-first sell-autonomia_web
```

Depois: repetir o smoke Hub2You, registrar sintoma, horário e saída no #437 e
em `PRODUCTION_CURRENT.md`. Sem migration nesta fase, então rollback não toca
o banco.

## Fase 3 — `api` e `asset-worker` (janela própria) 🔴

Só depois de A estável por 24 h e com OK novo. Resumo, para ser detalhado no
mesmo formato se aprovado:

1. Backup: spec + nomes das variáveis de `api` e `asset-worker`, `pg_dump -Fc`
   validado por `pg_restore --list`, cabeça do Alembic (tem que ser `0061`).
2. Imagem `easypanel/sell-autonomia/api:$SHA` com `Dockerfile.api`; o worker
   usa a mesma imagem. O `pdf-sandbox` **não** muda.
3. `docker service update --image … --update-order stop-first sell-autonomia_api`
   — conferir que a graça segue em `5m30s` (o `update` não pode zerá-la).
4. Worker: `docker service update --image … sell-autonomia_asset-worker` lendo
   antes `--limit-memory` atual (4 GiB) — **não** usar `ensure-asset-worker.sh`,
   que aborta com o serviço existente e declara 1 GiB.
5. Ready interno da API (`/health/ready` por `docker exec`, ver
   `PRODUCTION_CURRENT.md`) com `database`, `storage`, `pdf_sandbox` e o campo
   novo `antivirus`; o contêiner consegue resolver `TXT current.cvd.clamav.net`.
6. Rollback: `docker service update --image easypanel/sell-autonomia/api:<anterior>`
   e o mesmo no worker. ETA ≈ 2–3 min (a graça de 330 s pode alongar o stop).

## Registro final

`PRODUCTION_CURRENT.md` recebe o bloco de evidência do runbook (issue, pr,
merge_sha, ci_run, previous_release, backup, migration_head, imagens, health,
smoke, rollback_decision) com o SHA **lido do host** depois do update. Depois
da conferência: apagar `/tmp/appsell-src-$SHA.tar.gz` local e do host; manter
`$D` até a Fase 3 fechar.
