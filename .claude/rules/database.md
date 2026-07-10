---
paths:
  - "**/*.sql"
  - "infra/migrations/**/*.sql"
---

# Database Rules

- Tratar migrations de produção como alto risco.
- Não executar writes em produção sem aprovação explícita.
- Toda migration deve ser idempotente quando possível.
- Incluir rollback ou estratégia de mitigação.
- Validar impacto em dados existentes.
- Para SQL manual, incluir primeiro o comando para entrar no psql.
