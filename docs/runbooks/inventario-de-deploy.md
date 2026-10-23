# Inventário de deploy — Autonom.ia Sell

Modelo: `chat2you/docs/runbooks/inventario-de-deploy.md` (a Orquestração Deploy
só assume a fila do projeto depois que este arquivo existe). Campo sem resposta
comprovada fica **a confirmar**. Fonte de cada linha entre parênteses.

Produção: `https://sell.autonomia.site`, VPS EasyPanel nativo (Docker Swarm),
projeto EasyPanel `sell-autonomia`, nó único `srv707880` (`85.31.60.100`).
Estado **lido do host em 23/10/2026** (Fase 0 do #437, só leitura):

| Serviço | Imagem (label `revision`) | UpdateConfig | Health |
|---|---|---|---|
| `web` | `web:fa13a78` | stop-first, pause, monitor 5s | imagem: `/api/health/startup` |
| `api` | `api:409ae23` | start-first, pause, monitor 5s; graça 5m30s | imagem: `/health/startup` |
| `asset-worker` | `api:409ae23` | stop-first, pause, monitor 5s; graça 30s | `--health-cmd` da spec |
| `pdf-sandbox` | `pdf-sandbox:d5e00ec` | stop-first, pause, monitor 5s | `NONE` |

`PRODUCTION_CURRENT.md` parou em 04/09 (`68ffd7b`). Houve releases em 12/09 e
uma atualização das tasks em 16/10 (21:12 UTC) sem registro. A lição para o
trem: o SHA vale o que o host responde, nunca o documento.

**Lacunas do inventário, abertas em 23/10:**

- **Deploy de 16/10, 21:12 UTC, sem registro.** As tasks de `web`, `api` e
  `asset-worker` foram atualizadas nessa hora (`UpdatedAt` da spec): o web
  saiu de `b387252` e foi para `fa13a78`, e a api saiu de `fa13a78` e foi para
  `409ae23`. Não há Issue, PR, comentário nem linha em `PRODUCTION_CURRENT.md`
  sobre isso. Quem subiu, por quê e com que validação: **a confirmar**.
- **Credencial do migrador:** o `bootstrap-native-90` não está no Swarm
  ([#456](https://github.com/autonom-ia/appsell/issues/456)).

| Campo | Resposta do Sell |
|---|---|
| Como sobe | **Manual.** Nada sobe sozinho: push na `main` só roda o App CI. Quem opera envia o fonte (`git archive`) para `/home/claudete/appsell-src-<sha>` no host, constrói as imagens lá com `--build-arg GIT_SHA=<sha>` e roda `docker service update --image … --update-order stop-first` serviço por serviço (`PRODUCTION_RELEASE_APPSSELL.md`, releases de 02–04/09 em `PRODUCTION_CURRENT.md`) |
| O que não dispara | Tudo, porque não há deploy automático. O workflow `EasyPanel native images` só constrói e ensaia imagens (manual ou toda segunda às 06:41 UTC) e **não publica** em lugar nenhum |
| Serviços e imagens | `sell-autonomia_web` ← `easypanel/sell-autonomia/web:<sha>` (`Dockerfile.web`) · `sell-autonomia_api` ← `easypanel/sell-autonomia/api:<sha>` (`Dockerfile.api`) · `sell-autonomia_asset-worker` ← a mesma imagem da api · `sell-autonomia_pdf-sandbox` ← `easypanel/sell-autonomia/pdf-sandbox:<sha>`, construída do mesmo `Dockerfile.api` · `sell-autonomia_bootstrap-native-90` ← `Dockerfile.bootstrap`, que guardava a credencial do migrador: **não aparece** no `docker service ls` de 23/10, onde está essa credencial hoje **a confirmar** (#456) · Do próprio projeto: `clamav`, `clamav-proxy`, `postgres`, `postgres-native-90`, `redis`, `redis-native-90`, `storage` e `view-storage` (**MinIO**; o CI usa SeaweedFS). Os serviços **não precisam andar no mesmo SHA** |
| Ordem | Padrão: **web → api → migration**, porque o web aceita o campo novo antes de a API expô-lo (`PRODUCTION_CURRENT.md`, release `89233e2`). Quando a API nova depende de função nova no banco, a migration vem primeiro (release `803fa82`). A ordem é decidida por release e escrita antes. `pdf-sandbox` **sempre** com `stop-first` e `--no-healthcheck` |
| Health checks | `web`: `HEALTHCHECK` do contêiner em `/api/health/startup`; tráfego em `/api/health/ready` (público, `https://sell.autonomia.site/api/health/ready`: `redis`, `api`, `storage_upload`). **Não existe** `/api/healthz`. `api`: `HEALTHCHECK` em `/health/startup`; `/health/ready` (`database`, `storage`, `pdf_sandbox`, `antivirus`) só por `docker exec` dentro do contêiner. O ready público do web **não prova** a API. `asset-worker`: `--health-cmd` próprio do serviço (`ensure-asset-worker.sh`) |
| Rollback | `docker service update --image easypanel/sell-autonomia/<serviço>:<sha-anterior> --update-order start-first sell-autonomia_<serviço>`, ou `docker service rollback sell-autonomia_<serviço>` (volta à `PreviousSpec`). **Em 23/10 o host guardava uma imagem só por serviço**: a anterior do web (`b387252`) e a da api (`fa13a78`) não existiam mais. Todo release registra o ID da imagem atual antes do update e proíbe `docker image prune` até fechar. Leva cerca de 1–2 min por serviço; a `api` pode levar até ~6 min, por causa da graça de 330 s. Sentido inverso da subida. `alembic downgrade` **não** é a primeira resposta, e a cadeia não desce inteira: a `0060` é forward-only (#425) |
| Migrations | Alembic em `apps/api/migrations`, aplicado à mão num contêiner efêmero da imagem da api na rede `easypanel`, com `APPSELL_MIGRATOR_DATABASE_URL` montada no host (`docker run --rm --network easypanel … alembic upgrade head`). Antes: `pg_dump -Fc` validado por `pg_restore --list`. Head registrado: `0061_o_atraso_alcanca_o_aluno` |
| Backup | Dump lógico antes de cada release, em `/tmp/sell-release-<sha>-<ts>` no host, junto com a spec dos serviços e os **nomes** das variáveis. Rotina diária cifrada off-host: scripts prontos em `deploy/easypanel-native/backup/`, rotina recorrente **a confirmar** (`BACKUP_RESTORE_SELL.md`) |
| Acesso à produção | SSH `claudete` no host (resolver `dig +short sell.autonomia.site` antes: em 31/08 o alias apontava para máquina desativada). Banco: `postgres-native-90`, base `appsell_native_90`, só pela rede do Swarm. Logs: `docker service logs sell-autonomia_<serviço>`; a API grava JSON, uma linha `http_request` por requisição (`TROUBLESHOOTING.md`). Logs fora do host: **não há** (#312) |
| Segredos e env | Na spec dos serviços Swarm, gerida pelo EasyPanel. Nunca usar `updateEnv` do painel sem o campo `env` completo (omitir apaga tudo). Comparar variáveis **por nome** antes e depois de cada `service update`. Nomes de referência: `deploy/easypanel-native/*.env.example` |
| Compartilha com | O host EasyPanel, a rede Docker `easypanel` e o Traefik com os outros projetos da VPS: **a confirmar** quais. Postgres e ClamAV são serviços do próprio projeto `sell-autonomia` |
| Fila | Ainda não há. Entra no trem da Orquestração Deploy por este inventário (#438) |
| Testes que travam | Hoje **nenhum check é obrigatório**: a `main` não tem branch protection. O App CI (`app-ci.yml`, job `ci-gate`) roda em PR, push na `main` e `workflow_dispatch`. A última execução na `main` (`409ae23`, 12/09) reprovou; a correção está no #422. Tornar `ci-gate` obrigatório é proposta da trilha Plataforma, e a decisão é do Rodrigo |
| Quem confere | Depois de cada release: health dos dois lados, logs sem `ERROR`/`Traceback`, smoke da Hub2You (`/s/hub2you` = 200 com "Ver e comprar" ×2; `/s/hub2you/p/condicoes-gerais` e `/s/hub2you/p/gerador-de-cotacoes-comparativas` = 200 com "Comprar agora" e `new_user=true`; `/auth/login?next=…&new_user=true` = 307 para o Identity, que dá 200; `/library` e `/auth/*` = 307) e prova do bundle novo por string servida. Registro em `PRODUCTION_CURRENT.md`, com o SHA lido do host |
| Quem aprova o quê | Merge, deploy, produção (inclusive leitura no host), migration, secrets, DNS, permissões e billing: **Rodrigo**, uma operação por vez (`AGENTS.md`). Rollback pré-escrito pode rodar sem novo OK se o safety abort disparar |
