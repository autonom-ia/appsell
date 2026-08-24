# Progresso histórico — jornada de publicação Seller P0/P1

- Issue: #188
- Branch: `codex/publish-ux-p0-p1`
- Última atualização do slice: 2026-08-23
- Estado histórico: implementação P0/P1 concluída na PR #189; depois, o fluxo foi
  sincronizado, corrigido e entregue no release da PR #201.

## Estado atual

Não use este arquivo como fotografia de produção. O editor, o upload, a
publicação e o acesso gratuito/pago foram incorporados ao release
`2c8a36897eadf624955810ffdaee7b33c6239334` e validados na VPS. A fonte atual é
[PRODUCTION_CURRENT.md](../../PRODUCTION_CURRENT.md); o acompanhamento da
documentação desta sessão está na Issue #202.

## Última posição verificável

- HEAD remoto atual: `aebf0a2` (`style: satisfy API CI formatting gate`), sobre `f674fbf` e a sincronização `bcfc682` com `origin/main`.
- Worktree atual: limpo nos arquivos rastreados; `.playwright-mcp/` permanece não rastreado e preservado.
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
- O nome físico do arquivo não é renderizado nos cartões, feedback, Preview, área do aluno ou seletor nativo; o input permanece acessível sobre um botão visual.
- Conflito 409 de oferta bloqueia nova tentativa até reconciliação e oferece `Atualizar dados da oferta` no próprio fluxo.
- Na execução original deste slice, nenhuma escrita foi feita em produção e a
  validação SQL foi feita em cluster local descartável. Isso foi superseded pelo
  smoke real posterior registrado em `PRODUCTION_CURRENT.md`.

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
- [x] P1.6 correção do nome no input nativo e recuperação de conflito de oferta
- [x] Review independente P1 — PASS final; timeout/cancelamento e nome físico foram rechecados; nenhum bloqueio P0-P3

## Evidências de verificação

| Gate | Evidência | Estado |
|---|---|---|
| Typecheck/lint | `pnpm typecheck`, `pnpm lint` | PASS |
| API/contrato | `47 passed, 1 skipped` em `test_api_catalog.py`; Ruff PASS | PASS |
| Upload unitário | `26 passed` em Vitest (asset + storage policy) | PASS |
| E2E Produto → Preview → Publicar | Chromium, 320 px: `1 passed`; suíte completa pós-correção: `19 passed` | PASS |
| E2E upload/responsividade/conflito | Chromium direcionado independente: `2 passed`; suíte completa inclui uploader, conflito e responsive | PASS |
| PostgreSQL/migrations 0027–0028 | cluster local descartável, migration head e produto/publicação: `44 passed` | PASS |
| Review independente P0 | Pauli: `PASS`, P0/P1/P2/P3 = 0; achados anteriores corrigidos | PASS |
| Review independente P1 | Pauli: `PASS`, P0/P1/P2/P3 = 0; picker, Preview/aluno e conflito rechecados | PASS |
| Vitest independente | asset/storage policy: `26 passed` | PASS |
| CI remoto da PR #189 | primeira execução bloqueou apenas por Ruff format; correção `aebf0a2` reenviada e `ci-gate` PASS | PASS |
| PR #189 | histórico do slice; supersedida pelo release posterior | histórico |
| PR #201 / Issue #195 | merge em `main`; SHA `2c8a36897eadf624955810ffdaee7b33c6239334` | PASS |
| Deploy VPS | web/api/worker ativos no EasyPanel nativo; health/readiness e smoke real verificados | PASS |
| Aceites do catálogo, tenants e acesso | `14/14` critérios de produção verificados; ver fonte atual | PASS |

## Registro de atualizações

- 2026-08-23 — auditoria concluída; Issue #188, branch, `plan.md` e este `progress.md` criados.
- 2026-08-23 — P0.1 iniciado: separação do contrato de produto/curso do contrato de oferta, preservando a oferta como etapa posterior.
- 2026-08-23 — contrato editorial, checklist, CTA, erros acionáveis, Preview e timeout de upload implementados; validação local direcionada verde.
- 2026-08-23 — E2E Chromium verde para Produto/Oferta/Preview/Publicar, uploader mobile/zoom e readiness sem Oferta; PostgreSQL isolado indisponível e mantido como pendência explícita.
- 2026-08-23 — migrações 0027/0028 aplicadas em PostgreSQL local descartável; o primeiro teste encontrou e corrigiu o limite de 32 caracteres da revision 0028.
- 2026-08-23 — timeout de confirmação passou a ser recuperável sem cancelar o upload ambíguo; Vitest 26/26, API 47/1 skip, typecheck, lint e Ruff passaram.
- 2026-08-23 — reviews independentes finais P0/P1: PASS; E2E final de uploader/responsividade: 2/2.
- 2026-08-23 — sincronização autorizada com `origin/main` concluída em `bcfc682`; conflitos resolvidos preservando o fluxo P0/P1.
- 2026-08-23 — revisão pós-sincronização encontrou e corrigiu dois pontos P1: nome residual no input nativo e conflito de oferta sem atualização em fluxo.
- 2026-08-23 — correção final validada por typecheck, lint, Vitest 26/26, E2E direcionado independente 2/2 e suíte Chromium completa 19/19; nenhum banco da plataforma foi usado.
- 2026-08-23 — correção final commitada em `f674fbf` e enviada para a branch da PR; CI remoto identificou somente formatação Ruff em dois arquivos.
- 2026-08-23 — formatação corrigida em `aebf0a2`; fast API local passou (format/check, Ruff, mypy, pip-audit e 34 testes) e o `ci-gate` remoto terminou PASS.
- 2026-08-23 — Project #3 atualizado para `Em review`, com próxima ação de solicitar aprovação explícita do merge final e depois planejar deploy na VPS.
- 2026-08-23 — PR #201 mergeada e release `2c8a36897eadf624955810ffdaee7b33c6239334` implantado na VPS; smoke real 14/14 PASS. Ver `docs/PRODUCTION_CURRENT.md`.
