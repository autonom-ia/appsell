# PROGRESS — Appsell

Última atualização documental: 2026-08-24.

## Fase atual

O Appsell saiu do estado de protótipo Local/CI e possui uma operação real
multitenant em produção no domínio `sell.autonomia.site`. A release comprovada
usa web `1d2ea631`, API/asset-worker `50b6301` e migration
`0033_pdf_study_state`.

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
- Leitor PDF premium com páginas protegidas, links de índice, vídeo interno,
  carregamento sob demanda, cache e retomada.
- Favoritos e marcações vetoriais privadas, editáveis e persistentes.
- Mão e Editar marcação validadas pelo Rodrigo em produção após o PR #225.

## Evidência final

| Gate | Evidência |
|---|---|
| CI | [run 32768382697](https://github.com/autonom-ia/appsell/actions/runs/32768382697), success |
| Merge | [PR #225](https://github.com/autonom-ia/appsell/pull/225), precedido pelos PRs #211, #215 a #219 e #223 do leitor |
| Produção | web `1d2ea631`; API/asset-worker `50b6301` |
| Health | readiness público com Redis, API e storage saudáveis; web healthy |
| Banco no smoke #195 | 3 sellers, 2 memberships, 3 produtos, 11 assets e 0 entitlements após transação revertida; não reutilizar como contagem atual |
| Aceite | #195 com 14/14 PASS; [Issue #224](https://github.com/autonom-ia/appsell/issues/224) validada na sessão real |

## Trabalho documental desta sessão

- Issue: [#226](https://github.com/autonom-ia/appsell/issues/226)
- Branch: `codex/226-prod-reader-handoff`
- Objetivo: consolidar o release do leitor, o aceite real e o ponto seguro de
  retomada para compact/clear.
- Limite: somente documentação; nenhum novo deploy ou mudança de runtime.

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

Após compact/clear, começar por `docs/PRODUCTION_CURRENT.md`. O próximo trabalho
de produto deve abrir nova Issue a partir de `origin/main`; não reutilizar as
branches dos PRs #211, #223 ou #225.

O histórico detalhado permanece nos commits anteriores, ADRs e arquivos
`docs/audit/`; este arquivo é o resumo atual para handoff.
