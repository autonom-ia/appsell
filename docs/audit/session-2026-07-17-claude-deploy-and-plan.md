# Audit — sessão Claude 2026-07-17 (deploy demo + plano de produção)

## Issue/Branch/PR
- Issue #90 → PR #91 **MERGEADA** (squash, aprovação condicional do Rodrigo cumprida via gate de review).
- Fix crítico: 756ef29 + 22fee6f (deadlock de markers do bootstrap; job CI `bootstrap-rehearsal` novo, 6 cenários).
- Épico de produção criado: #92 (blocos #93–#97).
- Em andamento: PR draft migration 022 `appsell-web` (Foundation) + PR draft rota bootstrap superadmin (`feat/93-superadmin-bootstrap-route`).

## Decision points
1. Gate de review multi-agente (codex+Claude) achou bloqueador determinístico no bootstrap → NO_GO → fix na causa raiz + reteste em Postgres descartável → GO. (Marker `appsell-demo-reset-target-v1` precisa existir ANTES do reset; conclusão nova = `appsell-native-90-complete-v1`.)
2. `bootstrap-native-90`: réplicas 0 em vez de destroy (regra destroyService/incidente 2026-05-22). Imagem contém PDFs-aula — follow-up de remoção.
3. Runbook executado no EasyPanel: bootstrap `native bootstrap=ready database=appsell_native_90` EXIT 0; serviços `api` e `web` criados (main, autoDeploy=false, capDrop ALL, recursos do README). `PORT=3000` adicionado ao env do web (EasyPanel injeta PORT=80; healthcheck do Dockerfile espera 3000 — causa raiz, não workaround).
4. Cutover `sell.autonomia.site` → web:3000 aprovado pelo Rodrigo (com Basic Auth). Rollback documentado em /tmp/sell-autonomia_pre-deploy-backup_2026-07-17T0845.json.
5. Login demo não completa em sell.autonomia.site: trava proposital (`lib/public-demo-runtime.ts` + teste "rejects the public production origin"). NÃO removida — Rodrigo redirecionou o objetivo para produção real.
6. Verificação PRD vs código (123 requisitos): 27 prontos / 44 parciais / 14 só-demo / 38 faltam. Épico #92.
7. **Opção B aprovada** (integrar com Foundation viva; tokens Cognito; adapter flexibilizado com security-review) vs Opção A (cadeia de drafts de tokens lógicos, 12–18d). Vault: certificar OpenBao é pré-requisito de qualquer caminho.

## Validação
- Playwright demo-personas 3/3 contra sslip.io (checkout cupom, PDF sha256, vídeo Range 206, superadmin revoke).
- Tester denso 24/24 (Basic Auth 401 gate, healthz com corpo, isolamento cross-tenant 404, out-of-scope 405/404, bypass persona 400, HSTS).
- Pós-cutover: sslip.io 24/24; sell.autonomia.site protegido (401 sem credencial em tudo).

## Riscos abertos
- Issue #88 (hardening VPS) sem dono — API do EasyPanel vaza env de todos os projetos para usuário autenticado.
- Backup Postgres/MinIO do Sell inexistente (PITR = item 8 do épico, paralelizável).
- view.autonomia.site é client vivo da Foundation — regressão obrigatória em qualquer mudança lá.

## Leva de merges 2026-07-17 (régua codex+verde do Rodrigo)
9 PRs mergeadas em main após review codex APROVADO + CI real verde:
- appsell #91 (deploy nativo), #102 (backup cifrado+restore), #101 (spec convites), #103 (adapter Opção B), #99 (bootstrap follow-up schema/seed), #106 (manifesto passkey), #104 (preço Offer, migration 0018), #100 (plano OpenBao), #98 (rota bootstrap superadmin).
- foundation #41 (client appsell-web, inactive, só host prod, passkey on).

Régua evitou de entrar em prod: 2 CRITICAL na #104 (PATCH 422 + vitrine quebrada), perda silenciosa de preço, reversão de status = outage de login (#41), bug rotação backup, query que falharia no cenário de drift, teste 409-vs-403 mal-especificado.

## Issues abertas nesta sessão
- appsell #92 (épico PRD) → #93-#97 (blocos 1-5), #105 (P0 segurança: MFA verificável, assurance auto-declarado), #107 (LOW: cap overflow bigint bootstrap).
- foundation #42 (P0: ledger de migrations — replay reverteria view-autonomia vivo).

## Próxima ação exata (tudo 🔴 execução vermelha, sob protocolo regra 3)
1. NÃO deployar Foundation até ledger #42 (senão reverte client view-autonomia vivo em prod). Depois: deploy Foundation (bump schemaVersion, workflow_dispatch gated) + PATCH appsell-web inactive→active.
2. OpenBao na VPS: resolver disco ~81% + backup (appsell#88) ANTES; provisionar conforme docs/plans/OPENBAO_PROD_PLAN.md (mergeado); depois PR de código certificando o provider (remove bloqueio NODE_ENV=production §5).
3. MFA verificável #105: Lambda PreTokenGeneration na Foundation + validação do claim na rota → destrava confiança do superadmin.
4. Usuário comercial@ no pool Cognito (🔴) + flip APPSELL_OAUTH_MODE=identity em staging → validar Opção B ponta a ponta → cutover.
5. Implementar convites (spec #101 mergeada): PR-1 precisa da linha pré-grant na AUTHORIZATION_MATRIX (baseline, 🟢) + secret INVITE_TOKEN_KEY.
6. Campo "Projeto"→opção Appsell criada; 8 itens atribuídos. Falta criar opção 'Épico' no Tipo (opcional).
