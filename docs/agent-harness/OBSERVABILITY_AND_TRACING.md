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

## Uso

A observabilidade deve alimentar:

- MEMORY_LEARNINGS.md
- revisão de PR
- melhoria de tools
- melhoria de skills
- melhoria de prompts
