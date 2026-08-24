# Runbook — release e rollback do Appsell em produção

Este runbook descreve o estado operacional comprovado na VPS do Appsell. Ele
não autoriza por si só novo deploy, mudança de secrets, banco ou cobrança.

## Ambiente atual

- Domínio: `https://sell.autonomia.site`
- Host: VPS EasyPanel nativo / Docker Swarm
- Serviços: `sell-autonomia_web`, `sell-autonomia_api`,
  `sell-autonomia_asset-worker`
- Banco: `appsell_native_90` no serviço Postgres nativo
- Release comprovada: `2c8a36897eadf624955810ffdaee7b33c6239334`
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
9. Validar logs recentes sem `ERROR`, `Traceback`, `FATAL` ou `panic`.

Não imprimir DSNs, chaves, tokens, senhas ou o ambiente completo dos serviços.

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
