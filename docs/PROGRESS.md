# PROGRESS — Appsell

Última atualização documental: 2026-08-24.

## Fase atual

O Appsell saiu do estado de protótipo Local/CI e possui uma operação real
multitenant em produção no domínio `sell.autonomia.site`. A release comprovada
está no SHA `2c8a36897eadf624955810ffdaee7b33c6239334`.

## Entregue

- Autenticação real e sessão protegida no ambiente de produção.
- Hierarquia de plataforma: `platform_superadmin` global, tenants e
  `seller_owner` isolados.
- Tenants reais Autonom.ia e Hub2You.
- Editor com sequência Produto → Currículo → Oferta → Preview → Publicar.
- Produto, curso, oferta, publicação versionada e materiais verificados.
- Link gratuito com cadastro/login, entitlement e biblioteca.
- Link pago com checkout obrigatório e fulfillment antes do entitlement.
- Player/aulas/PDFs protegidos por buyer + seller + produto + publicação.
- Isolamento negativo entre tenants e bloqueio do comprador no editor.
- Entitlement gratuito idempotente.
- Backup pré-cutover e tags de rollback na VPS.
- PR #201 mergeada; CI pós-merge verde; deploy real validado.
- Issue #195 encerrada com 14/14 aceites PASS.

## Evidência final

| Gate | Evidência |
|---|---|
| CI | [run 32656218821](https://github.com/autonom-ia/appsell/actions/runs/32656218821) — success |
| Merge | [PR #201](https://github.com/autonom-ia/appsell/pull/201) |
| Produção | web/API/asset-worker em `2c8a3689` |
| Health | API `/health/ready` com database, storage e pdf sandbox saudáveis |
| Banco | 3 sellers, 2 memberships, 3 produtos, 11 assets, 0 entitlements persistidos |
| Aceite | [Issue #195](https://github.com/autonom-ia/appsell/issues/195), 14/14 PASS |

## Trabalho documental desta sessão

- Issue: [#202](https://github.com/autonom-ia/appsell/issues/202)
- Branch: `codex/202-docs-production-handoff`
- Objetivo: consolidar o estado real e separar produção de Local/CI/demo.
- PR: será aberta após revisão dos documentos alterados.

## Pendências reais

- Recebimentos/Stripe por tenant e operação financeira continuam separados do
  catálogo/publicação.
- Staging próprio, RPO/RTO formal e política de refund/chargeback ainda não são
  aceites de produção.
- Há Issues históricas abertas sobre slices anteriores; elas não devem ser
  interpretadas como regressão desta release sem reprodução no SHA atual.

## Regra de retomada

Antes de alterar código ou banco, revalidar branch, `origin/main`, SHA das
imagens, migration head, backup e comportamento público. Não usar documentos de
auditoria de julho como fotografia atual. Não executar reset/seed demo no banco
de produção.

O histórico detalhado permanece nos commits anteriores, ADRs e arquivos
`docs/audit/`; este arquivo é o resumo atual para handoff.
