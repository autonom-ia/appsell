# Trilhas e posse de pastas

Cada pasta tem **um** dono. A trilha só escreve no que é seu. Precisa mexer fora:
abre a Issue com o rótulo da trilha dona, ou pede pela Orquestração Sell, que
decide conflitos. **Arquivo que não aparece aqui não tem dono: pergunte à
Orquestração Sell antes de mexer**, e ela atualiza esta tabela.

| Trilha | Rótulo | Pastas e arquivos |
|---|---|---|
| Orquestração Sell | `trilha:orquestracao` | `docs/programa/`, `AGENTS.md`, `CLAUDE.md`, `docs/DECISIONS.md` (registro) |
| Qualidade e Segurança | `trilha:qualidade` | `.claude/agents/`. Revisa e testa tudo; não escreve funcionalidade nem o teste da trilha |
| 1 · Plataforma | `trilha:plataforma` | `.github/`, `scripts/` (inclui `scripts/ci/`), `deploy/`, `infra/`, `compose.yaml`, `next.config.ts`, `proxy.ts`, configs de teste (`vitest*.config.ts`, `playwright*.config.ts`), `apps/api/src/appsell_api/main.py`, `db.py`, `config.py`, `service_auth.py`, `internal_requests.py`, `apps/api/alembic.ini`, `docs/runbooks/`, `docs/PRODUCTION_CURRENT.md` |
| 2 · Dinheiro | `trilha:dinheiro` | `app/api/checkout/`, `app/api/registration-checkout/`, `app/api/demo-checkout/`, `app/api/webhooks/`, `app/api/financial/`, `app/(protected)/checkout/`, `app/(protected)/settings/payouts/`, `app/c/`, `lib/payments/` (novo), `lib/stripe*`, `lib/financial*`, `lib/checkout*`, `lib/payout-status.ts`, `lib/offer-price.ts`, `lib/appsell-pricing.ts`, `app/components/checkout-workspace.tsx`, `stripe-checkout-card.tsx`, `payout-connect-card.tsx`, `demo-checkout-workspace.tsx`, na api `checkout_routes.py`, `provision_checkout.py`, `configure_seller_financial.py`, `payout_routes.py`, `payout_sync_routes.py`, `fulfillment_worker.py`, `demo_checkout_worker.py`, `webhook_*`; no repo Financial: checkout externo e, na Onda 3, `src/credits/` |
| 3 · Loja, Produto e Membros | `trilha:membros` | `app/s/`, `app/(protected)/products/`, `app/(protected)/library/`, `app/(protected)/settings/` (exceto `payouts/`), `app/auth/`, `app/api/me/`, `app/api/library/`, `app/api/sellers/`, `app/api/storefronts/`, `app/api/session/`, componentes de produto, currículo, aula, loja e biblioteca em `app/components/`, `lib/members/` (novo), `lib/learning-*`, `lib/oauth*`, `lib/identity-*`, `lib/session-*`, `lib/buyer-*`, `lib/store-*`, `lib/product-*`, `lib/public-storefront.ts`, `lib/email/`, na api `catalog_routes.py`, `routes.py`, `public_routes.py`, `seller_settings_routes.py`, `cover_routes.py`, `learning_routes.py`, `email_worker.py`, `email_layout.py`; repos de Identity |
| 4 · Crescimento e Dados | `trilha:crescimento` | `app/(protected)/dashboard/`, `app/components/dashboard*`, `lib/dashboard-*`, `lib/tracking/`, `lib/reports/` (novos); ponte com o Chat2You (PRs lá entram pela fila de lá) |
| 5 · Checkout e Funil | `trilha:checkout` | Recebe da trilha 2 `app/api/checkout/`, `app/(protected)/checkout/`, `app/c/` e os componentes de checkout **quando** o PR que cria `lib/payments/` estiver no ar e a Orquestração Sell registrar a passagem nesta tabela |
| 6 · Mídia e Comunidade | `trilha:midia` | na api `asset_*`, `storage_*`, `video_*`, `pdf_*`; `app/components/lesson-asset-panel.tsx` e `lesson-material-panel.tsx` (player e upload), `lib/asset-media.ts`, `lib/private-learning-asset.server.ts`, `lib/protected-pdf-links.server.ts`, `lib/pdf-study*`, `lib/community/` (novo) |
| Plataforma (superadmin) | `trilha:plataforma` | `app/(protected)/platform/`, `app/api/platform/`, `platform_routes.py`, `platform_bootstrap_routes.py` |

**Controle de acesso e cripto** (`lib/csrf.ts`, `mutation-protection.server.ts`,
`access-link-token.server.ts`, `post-login-redirect.ts`, `rbac-roles.ts`,
`token-vault*`, `*-key-envelope-provider.server.ts`, `internal-envelope.ts`,
`crypto.ts`): dono é a trilha 3 (os de login) ou a Plataforma (os de chave e
envelope), mas **toda** mudança neles passa pelo `sell-security-reviewer` e pelo
OK do Rodrigo.

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
| `0062` | — | — | livre |

Head atual da `main`: `0061_o_atraso_alcanca_o_aluno`.
