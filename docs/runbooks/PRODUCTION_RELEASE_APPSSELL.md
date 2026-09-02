# Runbook — release e rollback do Appsell em produção

Este runbook descreve o estado operacional comprovado na VPS do Appsell. Ele
não autoriza por si só novo deploy, mudança de secrets, banco ou cobrança.

## Serviço `api`: período de graça no encerramento

O worker de e-mail pode estar no meio de um envio SMTP quando o deploy começa.
O `api` **precisa** de `--stop-grace-period 330s` (lease de 300 s + folga),
aplicado no mesmo `docker service update` que liga o canal — o comando
completo, com imagem, variáveis e graça numa atualização só, está em
`docs/architecture/EMAIL_POLICY.md` §8.1. Conferir antes de qualquer release
que toque o serviço:

```sh
docker service inspect sell-autonomia_api --format '{{.Spec.TaskTemplate.ContainerSpec.StopGracePeriod}}'
```

Valor menor que isso troca "deploy" por "e-mail duplicado" na caixa de quem
comprou. Detalhe em `docs/architecture/EMAIL_POLICY.md` §8.1.

## Ambiente atual

- Domínio: `https://sell.autonomia.site`
- Host: VPS EasyPanel nativo / Docker Swarm. **Resolva o DNS antes de operar**
  (`dig +short sell.autonomia.site`): em 31/08/2026 o alias de SSH usado em
  sessões anteriores apontava para uma máquina desativada, e a operação começou
  procurando os serviços no host errado.
- Serviços: `sell-autonomia_web`, `sell-autonomia_api`,
  `sell-autonomia_asset-worker`
- Banco: `appsell_native_90` no serviço Postgres nativo
- Release comprovada: api, worker e `pdf-sandbox` em `d5e00ec`; `web` em
  `9ebb93f`. Os serviços **não precisam andar no mesmo SHA**: um release que
  toca só o Next sobe apenas o `web`.
- Health interno da API: `/health/ready`

AWS/EC2 não é o destino deste release do Appsell. Não substituir a VPS pelo
ambiente AWS sem uma nova decisão de infraestrutura.

## Pré-deploy obrigatório

1. Confirmar Issue, PR aprovada e CI verde no SHA exato.
2. Confirmar `git rev-parse HEAD` e a origem do artefato.
3. Fazer dump lógico do Postgres e validar `pg_restore --list`.
4. Guardar o digest/tag anterior como rollback.
5. Confirmar que não há migration pendente inesperada.
6. Construir tags imutáveis de web e API com o SHA curto.
7. Atualizar API e worker de forma controlada; depois atualizar web.
8. Aguardar convergência dos serviços e executar healthcheck.
9. Validar logs recentes sem `ERROR`, `Traceback`, `FATAL` ou `panic`. O log
   da API é JSON em produção e traz uma linha `http_request` por requisição —
   ver [TROUBLESHOOTING](../TROUBLESHOOTING.md). Ausência de linha **não** é
   ausência de erro: a instrumentação por caminho ainda é parcial.

Não imprimir DSNs, chaves, tokens, senhas ou o ambiente completo dos serviços.

## Release que toca só o `web`

Quando a mudança é apenas do Next (página pública, telas, rotas do BFF), não há
migration, não há imagem de API e o caminho encurta:

1. Confirmar CI verde no SHA exato e fazer merge.
2. Salvar a spec do serviço e a tag da imagem atual como rollback.
3. Enviar o fonte para `/home/claudete/appsell-src-<sha>` e construir com
   `Dockerfile.web`, passando `--build-arg GIT_SHA=<sha>`.
4. `docker service update --image ... --update-order stop-first sell-autonomia_web`.
5. Se houver variável nova, adicioná-la **na mesma atualização**, com
   `--env-add`, para um único `stop-first`. Nunca use o `updateEnv` do painel
   sem o campo `env` completo: omitir apaga todas as variáveis.
6. Conferir o contador de variáveis **comparando nomes**, não contando
   entradas: uma variável duplicada na spec antiga faz o total encolher sem que
   nada tenha sido perdido.
7. Smoke da página: home, `/auth/signup`, `/auth/login`, `robots.txt`,
   `sitemap.xml`, `/api/health/live` e `/api/health/ready` (não existe
   `/api/healthz` no `web`), e o `POST /api/contato` se o formulário estiver
   no ar. Prova do bundle novo: uma string que só existe depois da mudança,
   buscada no CSS ou no HTML servido — health verde não prova nada sobre o
   bundle.

O rollback é uma linha, e a imagem anterior continua no host:

```bash
docker service update --image easypanel/sell-autonomia/web:<sha-anterior> \
  --update-order stop-first sell-autonomia_web
```

## Smoke pós-deploy

### Catálogo e autorização

- superadmin lista `autonomia` e `hub2you`;
- superadmin abre/edita produto de cada tenant;
- owner Hub2You abre seu produto sem 404;
- Produto → Currículo → Oferta → Preview → Publicar carrega;
- owner Hub2You é negado no produto Autonom.ia;
- buyer não abre nem altera o editor.

### Distribuição

- link gratuito redireciona para cadastro/login e retorna à biblioteca;
- replay do acesso gratuito não duplica entitlement;
- link gratuito não abre checkout;
- link pago exige checkout e não libera conteúdo antes da confirmação;
- aula e PDF só são autorizados via biblioteca/entitlement.

### Dados

- tenants, memberships, produtos, assets e PDFs existentes preservados;
- nenhum entitlement de teste persistido;
- nenhum seed/reset demo executado em produção.

## Rollback

Acionar rollback se health, convergência, autorização, conteúdo ou dados falhar.

1. Pausar a progressão do release e registrar o sintoma/horário.
2. Restaurar as imagens anteriores por tags/digests imutáveis.
3. Reverter API e worker antes da web quando houver mudança de contrato.
4. Não executar `alembic downgrade` destrutivo como primeira resposta.
5. Se houver alteração de schema/dados, restaurar o dump em banco separado,
   validar e só então decidir o cutover.
6. Repetir health, logs e smoke negativo/positivo.
7. Registrar causa raiz, SHA afetado e decisão no Issue.

O rollback da entrega de 2026-08-23 tinha as tags anteriores do release
`64ab9956` e o backup lógico criado antes do cutover. O caminho exato e os
nomes devem ser revalidados no host antes de uma nova operação; não reutilizar
um caminho antigo sem confirmar que o arquivo existe e está íntegro.

## Backup e restore

- Backup pré-cutover da entrega: `/home/claudete/backups/appsell/`.
- O dump foi mantido com permissão restrita e passou por validação de lista.
- Restore deve ser ensaiado em banco/volume novo; backup não é operação de
  rotina para apagar ou sobrescrever o banco atual.
- Ver [runbook geral de backup/restore](BACKUP_RESTORE_SELL.md) e os scripts em
  [`deploy/easypanel-native/backup`](../../deploy/easypanel-native/backup/README.md).

## Evidência de uma release

Registrar sempre:

```text
issue:
pr:
merge_sha:
ci_run:
previous_release:
backup:
migration_head:
web_image:
api_image:
worker_image:
health:
smoke:
rollback_decision:
```
