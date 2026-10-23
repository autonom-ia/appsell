# Trilhas e posse de pastas

Cada pasta tem **um** dono. A trilha só escreve no que é seu. Precisa mexer fora:
abre a Issue com o rótulo da trilha dona, ou pede pela Orquestração Sell, que
decide conflitos. Arquivo que não aparece aqui é da Plataforma até ser atribuído.

| Trilha | Rótulo | Pastas e arquivos |
|---|---|---|
| Orquestração Sell | `trilha:orquestracao` | `docs/programa/`, `AGENTS.md`, `CLAUDE.md`, `docs/DECISIONS.md` (registro) |
| Qualidade e Segurança | `trilha:qualidade` | `.claude/agents/`, suítes de isolamento e segurança (`tests/bff-*.test.ts`, a fiação delas no CI é da Plataforma); revisa tudo, não escreve funcionalidade |
| 1 · Plataforma | `trilha:plataforma` | `.github/`, `deploy/`, `infra/`, `scripts/`, `compose.yaml`, `next.config.ts`, `proxy.ts`, `apps/api/src/appsell_api/main.py`, `db.py`, `config.py`, mecânica de migrations (`alembic.ini`, `schema_ready`), `docs/runbooks/`, `docs/PRODUCTION_CURRENT.md` |
| 2 · Dinheiro | `trilha:dinheiro` | `app/api/checkout/` (até a passagem para a trilha 5), `app/api/webhooks/`, `app/api/financial/`, `lib/payments/` (novo), `lib/stripe*`, `lib/financial*`, `lib/checkout*`, `lib/payout-status.ts`, `apps/api/src/appsell_api/configure_seller_financial.py`, `payout_routes.py`, `fulfillment_worker.py`, `webhook_*`; no repo Financial: checkout externo e, na Onda 3, `src/credits/` |
| 3 · Membros e Acesso | `trilha:membros` | `app/(protected)/`, `app/s/`, `app/auth/`, `lib/members/` (novo), `lib/learning-*`, `lib/oauth*`, `lib/identity-*`, `lib/session-*`, `lib/buyer-*`, e-mail (`lib/email/`, `email_worker.py`, `email_layout.py`), `learning_routes.py`; repos de Identity |
| 4 · Crescimento e Dados | `trilha:crescimento` | `lib/tracking/`, `lib/reports/` (novos), `app/components/dashboard*`, `lib/dashboard-*`; ponte com o Chat2You (PRs lá entram pela fila de lá) |
| 5 · Checkout e Funil | `trilha:checkout` | `app/api/checkout/` e página de checkout, depois da passagem da trilha 2 |
| 6 · Mídia e Comunidade | `trilha:midia` | `apps/api/src/appsell_api/asset_*`, `storage_*`, `video_*`, `pdf_*`, player, `lib/community/` (novo) |

Arquivos de uso comum (`app/layout.tsx`, `app/styles.css`, `package.json`,
`pnpm-lock.yaml`, `apps/api/pyproject.toml`): mudança vai em PR próprio e
pequeno, avisado à Orquestração Sell antes, para não colidir com outro lote.

## Reserva de migrations

Cadeia única do Alembic. Antes de escrever uma migration, a trilha pede o número
à Orquestração Sell, que anota aqui. Uma reserva vale por 7 dias sem PR aberto.

| Número | Trilha | Issue | Estado |
|---|---|---|---|
| `0062` | — | — | livre |

Head atual da `main`: `0061_o_atraso_alcanca_o_aluno`.
