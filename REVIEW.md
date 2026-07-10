# REVIEW.md

## Objetivo

Guiar revisão de PRs por humano ou agente reviewer.

## Verificar

- Issue relacionada.
- Escopo claro.
- Sem mudança fora de escopo.
- Testes executados ou justificativa.
- Risco descrito.
- Rollback descrito.
- Project atualizado.
- Sem segredo exposto.
- Sem alteração perigosa sem aprovação.
- Documentação atualizada quando necessário.

## Severidade

- Crítico: risco de produção, segurança, dados, auth, billing, secrets.
- Alto: bug provável, regressão, teste ausente em área crítica.
- Médio: documentação faltando, escopo ambíguo, rollback fraco.
- Baixo: clareza, nomenclatura, pequenos ajustes.
