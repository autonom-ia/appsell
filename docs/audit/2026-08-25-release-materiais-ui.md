# Release 2026-08-25 (segundo) — interface de material complementar (#235)

```text
issue:             #235 (part of #228)
pr:                #241
merge_sha:         50f584951b01e7d01e827c1e20b875d3bbc172b4
ci_run:            32848012053 — success
previous_release:  3ddd58b (mesmo dia)
backup:            <pasta-de-backups>/appsell_native_90_20260825T124204Z.dump
                   950.652 bytes · permissão 600
migration_head:    0036_lesson_material_links (INALTERADO — release sem migration)
web_image:         easypanel/sell-autonomia/web  @ 50f5849
api_image:         easypanel/sell-autonomia/api:50f5849
worker_image:      easypanel/sell-autonomia/api:50f5849
health:            api  {"status":"ok","database":true,"storage":true,"pdf_sandbox":true}
                   web  {"status":"ok","redis":true,"api":true,"storage_upload":true}
rollback_decision: não acionado
```

## Ordem executada

1. Conferência do estado real: SHA `3ddd58b`, banco em `0036`.
2. Backup lógico `pg_dump -Fc` antes de qualquer alteração.
3. Deploy da api pelo EasyPanel; convergência e health interno confirmados.
4. `asset-worker` apontado para `easypanel/sell-autonomia/api:50f5849`.
5. Deploy da web; convergência confirmada.
6. Healthz público, e logs de api, web e asset-worker sem `ERROR`, `Traceback`,
   `FATAL` ou `panic` em 5 minutos.
7. Dados conferidos: `tenants=3 produtos=3 assets=11 aulas=6 entitlements=1`,
   `schema_ready()` = `t`.

## Achados de revisão fechados antes do merge

- **Segurança, MEDIUM:** `safeLessonLink` aceitava userinfo, então
  `https://suporte.exemplo.com@evil.example/` passava como link de aula. Recusado.
- **TypeScript, HIGH:** o diálogo de remoção fechava no `finally`, portanto uma
  falha sumia da tela exatamente como um sucesso.
- **TypeScript, MEDIUM:** a chave de idempotência sobrevivia à queda de conexão
  com os campos ainda editáveis; agora é casada com o payload.
- O contrato de embed vivia em três arquivos byte-idênticos; virou um módulo com
  teste fixando os dois prefixos literais.

## O que continua fora do ar ou inerte

- **#237:** editor ainda é trilho de cinco etapas, sem autosave nem coluna de
  prontidão; o painel legado de status da oferta segue como segunda porta para
  ativar venda.
- **#242:** o player entrega URLs de material de aulas que o aluno não abriu.
  Exige migration, por isso ficou fora da #241.
- **#232:** capa, catálogo e vocabulário.
