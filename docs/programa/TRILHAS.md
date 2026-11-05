# Trilhas e posse de pastas

Cada pasta tem **um** dono. A trilha só escreve no que é seu. Precisa mexer fora:
abre a Issue com o rótulo da trilha dona, ou pede pela Orquestração Sell, que
decide conflitos. **Arquivo que não aparece aqui não tem dono: pergunte à
Orquestração Sell antes de mexer**, e ela atualiza esta tabela.

| Trilha | Rótulo | Pastas e arquivos |
|---|---|---|
| Orquestração Sell | `trilha:orquestracao` | `docs/programa/`, `AGENTS.md`, `CLAUDE.md`, `docs/DECISIONS.md` (registro) |
| Qualidade e Segurança | `trilha:qualidade` | `.claude/agents/`. Revisa e testa tudo; não escreve funcionalidade nem o teste da trilha |
| 1 · Plataforma | `trilha:plataforma` | `lib/csrf.ts`, `lib/mutation-protection.server.ts`, `lib/internal-envelope.ts`, `lib/crypto.ts`, `lib/token-vault*`, `lib/*-key-envelope-provider.server.ts`, `.github/`, `scripts/` (inclui `scripts/ci/`), `deploy/`, `infra/`, `compose.yaml`, `next.config.ts`, `proxy.ts`, configs de teste (`vitest*.config.ts`, `playwright*.config.ts`), `apps/api/src/appsell_api/main.py`, `db.py`, `config.py`, `service_auth.py`, `internal_requests.py`, `apps/api/alembic.ini`, `docs/runbooks/`, `docs/PRODUCTION_CURRENT.md` |
| 2 · Dinheiro | `trilha:dinheiro` | `app/api/checkout/`, `app/api/registration-checkout/`, `app/api/demo-checkout/`, `app/api/webhooks/`, `app/api/financial/`, `app/(protected)/checkout/`, `app/(protected)/settings/payouts/`, `lib/payments/` (novo), `lib/stripe*`, `lib/financial*`, `lib/checkout*`, `lib/payout-status.ts`, `lib/offer-price.ts`, `lib/appsell-pricing.ts`, `lib/product-checkout-availability.server.ts`, em `app/components/`: `checkout-workspace.tsx`, `stripe-checkout-card.tsx`, `payout-connect-card.tsx`, `demo-checkout-workspace.tsx`; na api `checkout_routes.py`, `provision_checkout.py`, `configure_seller_financial.py`, `payout_routes.py`, `payout_sync_routes.py`, `fulfillment_worker.py`, `demo_checkout_worker.py`, `webhook_*`; no repo Financial, só código novo e isolado; mudar o que existe exige análise de impacto e OK do Rodrigo antes (AGENTS.md §7) |
| 3 · Loja, Produto e Membros | `trilha:membros` | `app/s/`, `app/c/` (resgate do link de acesso), `app/(protected)/products/`, `app/(protected)/library/`, `app/(protected)/settings/` (exceto `payouts/`), `app/auth/`, `app/api/me/`, `app/api/library/`, `app/api/sellers/`, `app/api/storefronts/`, `app/api/session/`, em `app/components/`: `access-link-*`, `access-links-panel.tsx`, `account-workspace.tsx`, `curriculum-workspace.tsx`, `free-access-workspace.tsx`, `lesson-description.tsx`, `library-workspace.tsx`, `product-card.tsx`, `product-draft-workspace.tsx`, `product-editor-workspace.tsx`, `product-headline-panel.tsx`, `product-image-panel.tsx`, `seller-onboarding-card.tsx`, `store-image-panel.tsx`, `store-settings-workspace.tsx`, `storefront-brand.tsx`, `settings-nav.tsx` e a UI comum (`app-shell.tsx`, `shell-profile-context.tsx`, `sell-brand.tsx`, `seller-space.ts`, `accessible-dialog.tsx`, `use-unsaved-changes.ts`); `lib/members/` (novo), `lib/learning-*`, `lib/oauth*`, `lib/identity-*`, `lib/session-*`, `lib/buyer-*`, `lib/store-*`, `lib/product-art.ts`, `lib/product-cover.ts`, `lib/access-link-token.server.ts`, `lib/post-login-redirect.ts`, `lib/rbac-roles.ts`, `lib/public-storefront.ts`, `lib/email/`, na api `catalog_routes.py`, `routes.py`, `public_routes.py`, `seller_settings_routes.py`, `cover_routes.py`, `learning_routes.py`, `email_worker.py`, `email_layout.py`; repos de Identity |
| 4 · Crescimento e Dados | `trilha:crescimento` | `app/(protected)/dashboard/`, `app/components/dashboard*`, `lib/dashboard-*`, `lib/tracking/`, `lib/reports/` (novos); ponte com o Chat2You (PRs lá entram pela fila de lá) |
| 5 · Checkout e Funil | `trilha:checkout` | Recebe da trilha 2 `app/api/checkout/`, `app/(protected)/checkout/`, `checkout-workspace.tsx` e `stripe-checkout-card.tsx` **quando** o PR que cria `lib/payments/` estiver no ar e a Orquestração Sell registrar a passagem nesta tabela |
| 6 · Mídia e Comunidade | `trilha:midia` | na api `asset_*`, `storage_*`, `video_*`, `pdf_*`; em `app/components/`: `learning-player.tsx`, `lesson-asset-panel.tsx`, `lesson-material-panel.tsx`, `protected-pdf-reader.tsx`, `protected-pdf-study.tsx`; `lib/asset-media.ts`, `lib/private-learning-asset.server.ts`, `lib/protected-pdf-links.server.ts`, `lib/pdf-study*`, `lib/community/` (novo) |
| Plataforma (superadmin) | `trilha:plataforma` | `app/(protected)/platform/`, `app/api/platform/`, `platform_routes.py`, `platform_bootstrap_routes.py` |

**Controle de acesso e cripto** (`csrf.ts`, `mutation-protection.server.ts`,
`internal-envelope.ts`, `crypto.ts`, `token-vault*`, `*-key-envelope-provider.server.ts`
da Plataforma; `access-link-token.server.ts`, `post-login-redirect.ts`,
`rbac-roles.ts`, `lib/oauth*`, `lib/identity-*`, `lib/session-*` e `app/c/` da
trilha 3): **toda** mudança neles passa pelo `sell-security-reviewer` e pelo OK
do Rodrigo.

**Testes:** quem muda o código escreve o teste, na pasta de teste correspondente
(`tests/`, `apps/api/tests/`). As suítes `tests/bff-*.test.ts` de isolamento são
escritas pela trilha e revisadas pela Qualidade; a fiação delas no CI é da
Plataforma.

**Arquivos de uso comum** (`app/layout.tsx`, `app/styles.css`, `package.json`,
`pnpm-lock.yaml`, `apps/api/pyproject.toml`, `apps/api/uv.lock`): mudança vai
em PR próprio e pequeno, avisado à Orquestração Sell antes. Dependência nova ou
atualizada exige o OK do Rodrigo.

## Reserva de migrations

Cadeia única do Alembic (`apps/api/migrations/versions/`). Antes de escrever, a
trilha pede o número à Orquestração Sell, que anota aqui. A trilha que reservou
escreve a migration **e** a entrada nova no `schema_ready`, e o PR avisa a
Plataforma. Uma reserva vale por 7 dias sem PR aberto.

| Número | Trilha | Issue | Estado |
|---|---|---|---|
| `0062` | 2 · Dinheiro | #465 | mergeada (#466) |
| `0063` | 2 · Dinheiro | #147 | PR #459 |
| `0064` | 2 · Dinheiro | #382 | PR #469 |
| `0065` | 2 · Dinheiro | #439 (identidade legal da loja) | reservada em 24/10 |

Head atual da `main`: `0062_mapping_do_produto_novo`.
