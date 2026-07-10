# Security Rules

- Nunca imprimir secrets, tokens, cookies, private keys ou database URLs.
- Não commitar `.env`.
- Mascarar valores sensíveis em logs.
- Validar autenticação, autorização e tenant isolation.
- Webhooks devem validar assinatura, idempotência e retry behavior.
- Não enfraquecer RLS, auth, billing ou audit logs sem aprovação explícita.
