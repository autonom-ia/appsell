# OBSERVABILITY_AND_TRACING.md

## Objetivo

Registrar o que os agentes fizeram, quais ferramentas chamaram e onde falharam.

## Campos mínimos

- session_id
- task_id
- issue_id
- repo
- branch
- agent_tool
- model
- agent_role
- tools_called
- skills_called
- commands_run
- errors
- validation_result
- decision_points
- next_action
- orchestration_owner
- review_round
- base_sha
- head_sha
- diff_hash
- join_status
- repair_wave
- finding_lifecycle_counts
- turns
- duration_seconds
- input_tokens
- output_tokens
- cache_read_tokens
- cache_write_tokens

Contadores indisponíveis devem ficar `null`, nunca estimados.

## Proibições

Nunca registrar:

- prompt ou resposta;
- conteúdo detalhado de finding de segurança;
- secret, cookie, credential ou authorization;
- valor de token;
- dado de cliente;
- URL com credencial.

Use `scripts/harness-review-ledger.sh` para validar o contrato sanitizado.

## Uso

A observabilidade deve alimentar:

- MEMORY_LEARNINGS.md
- revisão de PR
- melhoria de tools
- melhoria de skills
- melhoria de prompts
