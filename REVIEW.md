# REVIEW.md

## Objetivo

Guiar revisão de PRs por humano ou agente reviewer.

## Verificar

- Head SHA e hash do diff registrados.
- Todos os reviewers previstos concluíram antes da correção.
- Findings consolidados por fingerprint.
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
- Re-review restrito ao delta e findings abertos.
- Uma única revisão ampla final.

## Limite

No máximo duas ondas de correção. Finding P1/P2 novo depois do limite exige `STOP_REPLAN`, atualização do mapa técnico e revisão do plano.

## Severidade

- Crítico: risco de produção, segurança, dados, auth, billing, secrets.
- Alto: bug provável, regressão, teste ausente em área crítica.
- Médio: documentação faltando, escopo ambíguo, rollback fraco.
- Baixo: clareza, nomenclatura, pequenos ajustes.
