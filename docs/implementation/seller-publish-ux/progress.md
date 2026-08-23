# Progresso — jornada de publicação Seller P0/P1

- Issue: #188
- Branch: `codex/publish-ux-p0-p1`
- Última atualização: 2026-08-23
- Estado: implementação P0/P1 e gates técnicos concluídos; pedido de aprovação enviado; merge/deploy continuam bloqueados por aprovação explícita

## Última posição verificável

- Worktree base auditado: apenas `.playwright-mcp/` não rastreado, preservado.
- Branch própria criada a partir de `6333a8e`.
- Issue #188 criada para este slice.
- Código atual confirma os bloqueios que motivaram o trabalho:
  - Produto novo nasce sem Oferta, mas o PATCH do editor valida `offer_title` mesmo na aba Produto.
  - `Ativar oferta` muda para um estado que a publicação rejeita; publicar catálogo e vender estão acoplados de forma confusa.
  - Preview atual mostra metadados e não o currículo/material.
  - Polling de upload continua após falhas de status sem limite total de UX.
- O contrato editorial agora é independente da Oferta; a migração `0027_product_course_draft_update` mantém tenant, owner, versão, idempotência e auditoria.
- A migração `0028_offer_activation_guard` exige catálogo publicado antes de ativar vendas; o identificador foi mantido dentro do limite de 32 caracteres do Alembic.
- O editor agora guia Produto → Currículo → Oferta → Preview → Publicar catálogo, com ativação de vendas separada.
- A verificação de material sai para estado recuperável após 15 segundos e não bloqueia a edição estrutural durante a análise.
- Nenhuma escrita foi feita em produção.

## Mini-entregas

### Auditoria e desenho

- [x] Código e testes existentes lidos.
- [x] Proposta P0/P1 convertida em critérios implementáveis.
- [x] Plano persistido em `plan.md`.
- [x] Issue e branch próprias criadas.

### P0

- [x] P0.1 contrato/salvamento independente
- [x] P0.2 checklist e progressão guiada
- [x] P0.3 separar catálogo de vendas
- [x] P0.4 erros acionáveis e foco
- [x] P0.5 tenant/estado preservados entre etapas
- [x] P0.6 testes P0 — API, fluxo Playwright de Produto/Oferta/Preview/Publicar e publicação sem Oferta bloqueada
- [x] Review independente P0 — PASS final; o primeiro review encontrou bloqueios de ativação/publicação, todos corrigidos

### P1

- [x] P1.1 estados do upload
- [x] P1.2 timeout/retry recuperável
- [x] P1.3 navegação durante verificação
- [x] P1.4 Preview coerente
- [x] P1.5 testes P1 — Vitest, uploader Playwright, responsividade e PostgreSQL isolado passaram
- [x] Review independente P1 — PASS final; os bloqueios de timeout/cancelamento identificados no primeiro review foram corrigidos

## Evidências de verificação

| Gate | Evidência | Estado |
|---|---|---|
| Typecheck/lint | `pnpm typecheck`, `pnpm lint` | PASS |
| API/contrato | `47 passed, 1 skipped` em `test_api_catalog.py`; Ruff PASS | PASS |
| Upload unitário | `26 passed` em Vitest (asset + storage policy) | PASS |
| E2E Produto → Preview → Publicar | Chromium, 320 px: `1 passed` | PASS |
| E2E upload/responsividade | Chromium: uploader + larguras aprovadas `2 passed`; fluxo de readiness/independência `4 passed` | PASS |
| PostgreSQL/migrations 0027–0028 | cluster local descartável, migration head e produto/publicação: `44 passed` | PASS |
| Review independente P0 | Pauli: `PASS`, P0/P1/P2/P3 = 0; achados anteriores corrigidos | PASS |
| Review independente P1 | Pauli: `PASS`, P0/P1/P2/P3 = 0; timeout/cancelamento rechecados | PASS |
| Aprovação para merge | pedido enviado; sincronização da branch ainda não autorizada | bloqueado por processo |
| Deploy VPS | não autorizado nesta etapa | bloqueado por processo |

## Registro de atualizações

- 2026-08-23 — auditoria concluída; Issue #188, branch, `plan.md` e este `progress.md` criados.
- 2026-08-23 — P0.1 iniciado: separação do contrato de produto/curso do contrato de oferta, preservando a oferta como etapa posterior.
- 2026-08-23 — contrato editorial, checklist, CTA, erros acionáveis, Preview e timeout de upload implementados; validação local direcionada verde.
- 2026-08-23 — E2E Chromium verde para Produto/Oferta/Preview/Publicar, uploader mobile/zoom e readiness sem Oferta; PostgreSQL isolado indisponível e mantido como pendência explícita.
- 2026-08-23 — migrações 0027/0028 aplicadas em PostgreSQL local descartável; o primeiro teste encontrou e corrigiu o limite de 32 caracteres da revision 0028.
- 2026-08-23 — timeout de confirmação passou a ser recuperável sem cancelar o upload ambíguo; Vitest 26/26, API 47/1 skip, typecheck, lint e Ruff passaram.
- 2026-08-23 — reviews independentes finais P0/P1: PASS; E2E final de uploader/responsividade: 2/2.
- 2026-08-23 — checklist final entregue e pedido explícito enviado; PR #189 segue `CONFLICTING` por divergência com `origin/main`, sem merge/deploy.
