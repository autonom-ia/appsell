# MEMORY_AGENTS.md — Padrões de comportamento de agentes

Padrões observados em sessões de Claude Code, Codex e Cursor — o que funcionou, o que falhou, o que mudar no harness. Append-only.

## Schema obrigatório

```markdown
## Padrão [ID]
Data: [YYYY-MM-DD]
Agente/plataforma: Claude Code | Codex | Cursor | outro
Modelo usado: fast | medium | strong
Contexto: [tipo de tarefa — debug, feature, refactor, ops, security review]
Comportamento observado: [o que o agente fez — fato, não julgamento]
Avaliação: funcionou bem | falhou | inconclusivo
Lição: [o que mudar no harness, prompt, ou skill]
Mudança aplicada em: [arquivo onde a lição virou regra | "ainda candidato"]
```

## Política

1. Preencher após sessão significativa (>30min ou marco entregue)
2. Não é log de tudo — só padrões generalizáveis (funcionou bem ou falhou com motivo claro)
3. Confirmar com Rodrigo antes de promover padrão a regra em MEMORY_CORE
4. Padrões podem ser positivos (o que repetir) ou negativos (o que evitar)

## Padrão 001 — Exemplo (substituir/adicionar)

Data: [YYYY-MM-DD]
Agente/plataforma: [ex: Claude Code]
Modelo usado: [ex: strong]
Contexto: [ex: design de skill v2 com workflow executável]
Comportamento observado: [ex: agente escreveu skill completa de 130 linhas seguindo template estritamente]
Avaliação: [ex: funcionou bem]
Lição: [ex: template padronizado (Quick path + Trigger + Workflow + ...) reduz variabilidade e melhora qualidade de skills]
Mudança aplicada em: [ex: docs/superpowers/specs/2026-05-25-harness-v2-design.md Layer 2 — Skills v2]
