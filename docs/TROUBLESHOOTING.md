# TROUBLESHOOTING

## Problema: criação de produto retorna 403 após atualização

### Sintoma

Uma sessão local já aberta lista tenants e produtos, mas o primeiro POST de
produto retorna `forbidden_mutation`.

### Causa provável

A sessão foi criada antes do cookie `appsell_product_csrf` existir. O cookie é
derivado da sessão e da ação no callback; não há fallback genérico.

### Diagnóstico read-only

Confirme que a sessão é anterior à Fase 4A e que o browser não possui o cookie
legível `appsell_product_csrf`. Nunca registre o valor do cookie.

### Correção

Saia e entre novamente no ambiente. Isso cria uma nova sessão e os cookies CSRF
específicos sem expor a sessão opaca ao JavaScript.

### Validação

Selecione explicitamente o tenant, crie um rascunho sintético e confirme resposta
201 seguida da listagem persistida.

### Rollback / mitigação

O comportamento é fail-closed. Não relaxe origem ou CSRF; mantenha a orientação
de novo login até existir uma estratégia versionada de renovação de cookies.

## Problema: edição ou publicação retorna 403 após atualização

Sessões locais criadas antes da Fase 4B não possuem os cookies legíveis
`appsell_product_update_csrf` e `appsell_product_publish_csrf`. Saia e entre
novamente; não copie, registre ou reutilize os valores. Os tokens são separados
por ação de propósito. Não relaxe Origin/CSRF para preservar sessões antigas.

## Problema: publicação retorna 409

- `draft_version_conflict`: use **Recarregar versão do servidor**; a UI preserva
  os campos locais e permite novo envio com as versões atuais.
- `no_unpublished_changes`: o snapshot vigente já representa o draft atual;
  edite e salve antes de publicar uma nova versão.
- `slug_conflict`: escolha outro endereço para o produto.

Timeout/503 preserva a chave idempotente para retry do mesmo corpo. Uma nova
edição cria uma nova intenção; não force novas chaves manualmente.

## Problema: o PDF abre sem botão nenhum

O aluno abre o guia e não há botões de vídeo nem de índice — sem erro na tela, e
sem nada nos logs.

**Causa quase certa: o serviço isolado não está respondendo.** A leitura dos
links passa por ele, e uma falha ali virava `return []` — que é indistinguível de
"este PDF não tem links". Desde `ee194bf` a leitura insiste quatro vezes quando o
serviço responde `429` (ocupado), mas continua devolvendo vazio se ele estiver
fora.

**Como confirmar:**

```bash
# o socket existe?
sudo docker volume inspect sell-autonomia_pdf_socket --format '{{.Mountpoint}}' | xargs sudo ls -la
# o extrator real, contra o PDF real, dentro do container da api
sudo docker exec <api> python -c "..."   # ver PRODUCTION_CURRENT.md
```

**Não confie no `healthz` para isto.** Ele já respondeu `pdf_sandbox: true`
durante um incidente em que o socket não existia — corrigido em `ee194bf`, mas a
lição fica: a verificação é abrir um PDF.

**Causa mais provável de o socket sumir:** um deploy do `pdf-sandbox` com
`--update-order start-first`. **Nunca use** nesse serviço. O conserto é
`docker service update --update-order stop-first --force sell-autonomia_pdf-sandbox`.

## Problema: a API entra em laço de reinício depois de uma migration

O `/health/ready` alimenta o `HEALTHCHECK` do contêiner. Uma migration que sobe a
versão do banco **sem estender `app_private.schema_ready()`** deixa o banco "não
pronto", o health reprova, e o Swarm derruba a task — em laço.

**Como confirmar:**

```bash
docker exec <postgres> psql -U appsell_native_admin -d appsell_native_90 \
  -tAc "select version_num||' | ready: '||app_private.schema_ready()::text from alembic_version;"
```

Se a versão for nova e `ready` for `false`, é isto. Toda migration precisa
estender as duas cópias da função (`schema_ready_base_0016` e `_0017`) — a 0043 e
a 0044 mostram o formato. `EXPECTED_MIGRATION` em `reset_demo_tenant.py` também
precisa acompanhar.

## Problema: documento grande falha no envio, ou o worker fica reiniciando

O worker segura, ao mesmo tempo, o PDF de origem, o pacote de páginas, uma cópia
do pacote feita na verificação e as páginas extraídas — 1.556 MiB no pior caso
com 300 páginas. O `/tmp` dele é `tmpfs`, e **`tmpfs` conta contra a memória do
cgroup**: um `/tmp` maior que o limite de memória é espaço que não existe.

Conferir o que o host tem, e não o que o script declara:

```bash
docker service inspect sell-autonomia_asset-worker \
  --format '{{json .Spec.TaskTemplate.Resources.Limits}}'
```

`ensure-asset-worker.sh` **aborta quando o serviço já existe**, então ele não
atualiza nada sozinho — e o valor declarado nele já esteve abaixo do que o host
tinha. Ao passar `--limit-memory`, leia o valor atual antes.
