# Multi-plataforma: Codex, ChatGPT e Claude

## Regra

Uma fonte principal + adaptadores por ferramenta.

## Arquivos

- `AGENTS.md`: base universal e Codex/OpenAI.
- `CLAUDE.md`: adaptador do Claude Code, importando `AGENTS.md`.
- `.claude/rules/`: regras específicas por tipo de arquivo.
- `docs/agent-harness/`: documentação canônica.
- `.github/`: templates, workflows e PRs.

## Por que não duplicar tudo

Se cada ferramenta tiver uma instrução grande própria, o conteúdo diverge.

O correto:

```text
AGENTS.md = regra principal
CLAUDE.md = complemento específico
docs/agent-harness = documentação longa
```
