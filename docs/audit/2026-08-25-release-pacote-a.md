# Release 2026-08-25 — Pacote A (ondas 1 a 3) em produção

```text
issue:             #228 (ondas #229 #230 #231)
pr:                #233, #234, #236, #238, #239
merge_sha:         3ddd58b9d2efe192185f0708fd024b5ca278ed7e
ci_run:            32841252106 (certificação completa, 6/6 escopos verdes)
previous_release:  50b6301 (banco em 0033_pdf_study_state)
backup:            /home/claudete/backups/appsell/appsell_native_90_20260825T114340Z.dump
                   881 KB · 828 objetos · pg_restore --list OK · 600
migration_head:    0033_pdf_study_state -> 0036_lesson_material_links
web_image:         easypanel/sell-autonomia/web  @ 3ddd58b
api_image:         easypanel/sell-autonomia/api:3ddd58b
worker_image:      easypanel/sell-autonomia/api:3ddd58b
health:            api  {"status":"ok","database":true,"storage":true,"pdf_sandbox":true}
                   web  {"status":"ok","redis":true,"api":true,"storage_upload":true}
rollback_decision: não acionado
```

## Ordem executada

1. Backup lógico `pg_dump -Fc` e validação com `pg_restore --list`.
2. Migrations aplicadas **antes** da troca de imagem, a partir de uma imagem
   construída no host no SHA exato, executando somente
   `alembic -c apps/api/alembic.ini upgrade head`.
3. `alembic_version` = `0036_lesson_material_links`, `app_private.schema_ready()` = `t`,
   três tabelas novas presentes.
4. Deploy da api pelo EasyPanel, convergência confirmada, health interno OK.
5. `asset-worker` apontado para `easypanel/sell-autonomia/api:3ddd58b`.
6. Deploy da web, convergência confirmada.
7. Healthz público, logs sem `ERROR`/`Traceback`/`FATAL`/`panic` em 5 min.

## Por que a migration não usou o serviço bootstrap

O `bootstrap-native-90` é inerte por construção (`CMD ["sleep","infinity"]`),
mas o script que ele carrega faz mais do que migrar: roda `configure_context_key`
e `reset_demo_tenant`. Construir a imagem no host e executar apenas o comando de
alembic mantém o passo de migrate reduzido ao que ele precisa ser, sem tocar na
configuração de nenhum serviço numa VPS compartilhada com n8n, Manu e Lili.

## Dados preservados

`tenants=3 produtos=3 assets=11 aulas=6 entitlements=1`; tabelas novas vazias.

## Higiene

DSN do migrator e clone temporário removidos do host após o uso. O backup
permanece com permissão 600.

## O que continua inerte em produção

- **#235**: o servidor aceita documento, link e vídeo como material, mas o
  vendedor não anexa nada pela tela e o aluno não vê nada. Falta a interface.
- **#237**: o editor segue em trilho de cinco etapas, sem autosave nem coluna
  de prontidão, e o painel legado de status da oferta continua sendo uma
  segunda porta para ativar venda.
