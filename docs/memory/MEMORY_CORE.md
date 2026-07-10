# MEMORY_CORE.md — Regras permanentes

Regras que raramente mudam. Append-only. Cada regra é numerada e tem motivo + origem + data de revisão.

## Regras base (sempre válidas)

- Português brasileiro nas respostas e docs internas.
- Rodrigo é CEO/product owner técnico da Autonom.ia e Hub2You.
- Fluxo padrão: Issue → Branch → PR → Project → Review → Approval → Merge → Deploy/Rollback plan.
- Project obrigatório: https://github.com/users/autonom-ia/projects/3
- Sem merge/deploy/produção sem aprovação explícita do Rodrigo.

## Schema para nova regra

```markdown
## Regra [ID]
Regra: [texto da regra em uma linha]
Motivo: [por que existe — incidente, decisão, requisito]
Origem: [incidente YYYY-MM-DD | decisão YYYY-MM-DD | revisão YYYY-MM-DD]
Revisado por Rodrigo: [YYYY-MM-DD]
```

## Regra 001 — Sem destructive op em endpoint Easypanel compartilhado

Regra: Nunca executar `destroyService`, `deleteService`, `drop*`, `purge*`, `wipe*` em endpoint TRPC Easypanel sem aprovação explícita Rodrigo + backup confirmado.
Motivo: VPS multi-tenant — Manu e Lili compartilham infra. Op destrutiva em endpoint compartilhado pode derrubar projeto não-alvo.
Origem: incidente 2026-05-22 (Lili + n8n perdidos; snapshot Hostinger salvou).
Revisado por Rodrigo: 2026-05-22

## Regra 002 — Nunca omitir campo `env` em updateEnv Easypanel

Regra: Em `updateEnv` Easypanel, sempre passar campo `env` completo com TODAS as vars atuais — omitir = WIPE de todas as vars.
Motivo: API Easypanel substitui o conjunto inteiro; omissão é apagar, não preservar.
Origem: incidente M11b (lição registrada na seção Faixas Vermelhas do CLAUDE.md global).
Revisado por Rodrigo: 2026-05-22
