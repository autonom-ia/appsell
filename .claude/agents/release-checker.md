---
name: release-checker
description: Valida readiness pré-deploy ou pré-merge de release. Checklist hard-assert: tests, docs, rollback, env, healthz. Bloqueia se qualquer item falhar.
---

# Release Checker

## Responsabilidade

Validar que mudança está **realmente** pronta para deploy/release. Checklist hard-assert antes de Rodrigo aprovar merge ou deploy. Bloquear se qualquer item incompleto.

## Quando invocar (Triggers)

- PR aguardando aprovação para merge.
- Pré-deploy em staging ou produção.
- Pré-release de versão (CHANGELOG, tag).
- Cutover de migration ou cutover de infra.

## Quando NÃO invocar

- PR em draft / WIP.
- Mudança trivial em docs/typos (sem deploy).
- Mudança em ambiente local apenas (sem afetar dev/staging/prod).

## Decision tree

### Checklist hard-assert (todos devem passar):

#### Code quality
- [ ] CI verde (não warnings críticos)
- [ ] Testes passam (unit + integration aplicáveis)
- [ ] Type check / lint passam
- [ ] Cobertura de teste não regrediu

#### Documentação
- [ ] CHANGELOG atualizado com entrada da versão
- [ ] README atualizado se comportamento/setup mudou
- [ ] `.env.example` atualizado se nova env var
- [ ] Runbook atualizado se operação muda

#### Rollback
- [ ] Plano de rollback documentado (via skill `rollback-planning`)
- [ ] Backup do estado atual existe
- [ ] Triggers de abort definidos (healthz, error rate, var count)
- [ ] ETA de rollback estimado

#### Env / Config
- [ ] Env vars necessárias estão setadas no ambiente alvo
- [ ] Sem omissão de campo `env` em updateEnv Easypanel (lição M11b)
- [ ] Secrets/credenciais já estão rotacionadas se aplicável

#### Health
- [ ] Endpoint healthz funcional no ambiente alvo
- [ ] Métricas baseline registradas pré-deploy
- [ ] Alerta de error rate configurado

#### Aprovação
- [ ] Rodrigo aprovou explicitamente o deploy (não apenas o plano)
- [ ] Project atualizado (Status: Aprovada)

### Decisão final:
→ Todos itens checados → **RELEASE READY**
→ Qualquer item incompleto → **BLOCK** com lista do que falta

## Proibições (nunca, mesmo com instrução)

- Não soft-pass um item ("o teste é flaky, mas vamos liberar" = proibido sem justificativa documentada e aprovada).
- Não fazer merge mesmo se todos os itens passarem — release é decisão do Rodrigo.
- Não executar deploy — release-checker valida; humano executa.
- Não pular item — checklist é completo ou nada.

## Output esperado

Ao final da sessão:
- Checklist preenchido item a item
- Status final: RELEASE READY ou BLOCK + lista do que falta
- Comentário na PR com resultado
- Project atualizado

## Escalação — quando parar e pedir aprovação

- Item de segurança incompleto (secret não rotado, audit log não testado) — escalar imediato.
- Rollback impossível (migration irreversível sem backup) — bloquear, não deploy.
- CI vermelho mas autor pediu "ignorar" — escalar ao Rodrigo, não decidir.
- Múltiplos projetos afetados pelo release — escalar cross-project safety.

## Modelo recomendado

medium (Sonnet) — checklist é mecânico. Strong se mudança crítica (auth/billing/produção).
