# mcp-readonly-postgres

MCP para introspecção segura de bancos Postgres — schema, EXPLAIN, contagens — sem acesso de escrita.

## Propósito

Permitir que agentes investiguem schema e planos de query em bancos de produção/staging **sem nenhuma capacidade de escrita ou exfiltração de dados**.

## Tools

### `describe_schema(db_alias)`
Lista tabelas, colunas, tipos e constraints.
- **Input:**
  - `db_alias` (string): alias do banco (ex: `claudete_ops`, `manu_db`) — resolve via env var no servidor MCP
- **Output:** schema completo: `[{ table, columns: [{ name, type, nullable, default, fk?, pk? }], indexes: [...], rls_policies: [...] }]`
- **Risco:** Baixo (apenas metadata; sem dados de usuário)

### `explain_query(db_alias, sql)`
Roda EXPLAIN sem executar a query.
- **Input:**
  - `db_alias` (string)
  - `sql` (string): SELECT ou EXPLAIN. **Rejeitado**: qualquer statement que não comece com `SELECT` ou `EXPLAIN`
- **Output:** plano de execução
- **Risco:** Baixo (não executa)
- **Validação**: middleware no MCP server rejeita statements não-SELECT antes de chegar ao banco

### `count_rows(db_alias, table)`
Contagem segura via SELECT COUNT(*).
- **Input:**
  - `db_alias` (string)
  - `table` (string): nome exato da tabela
- **Output:** `{ count: N }`
- **Risco:** Baixo

### Tools NÃO disponíveis (proibidas)

- `query(db_alias, sql)` — retorno de dados de usuário **não** está nesta v1. Adicionar exige política dedicada (PII masking, rate limit, audit explícito).
- `execute(db_alias, sql)` — escrita **nunca** será disponibilizada neste MCP. Use migrations ou tool separado com aprovação humana.

## Enforcement

Dois níveis independentes — comprometer um não compromete o outro:

### Nível 1: Infraestrutura (DB user)
- Usuário Postgres criado com `GRANT SELECT` apenas
- Sem `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`
- Sem acesso a `pg_catalog.pg_authid` (proibir leitura de hashes de senha)
- Bind a schemas específicos quando aplicável

### Nível 2: MCP server middleware
- Antes de enviar SQL ao banco: regex **case-insensitive** `^\s*(SELECT|EXPLAIN|WITH)\b` — rejeita o resto. Lowercase (`select`), mixed (`SeLEcT`), e CTE (`WITH ... SELECT`) são aceitos.
- Statement composto (semicolon-separated) é rejeitado em sua totalidade — agente envia 1 statement por call.
- Comentários SQL no início (`-- ...` ou `/* ... */`) são strip antes do match.
- Sem fallback automático para outro user (se SELECT for negado, retorna erro — não escala privilégio).
- Log de cada tool call em `docs/audit/mcp-readonly-postgres.jsonl` com `{ ts, db_alias, tool, sql_hash, success }`.

## Credenciais

- Connection string resolvida server-side a partir de `db_alias` → env var (ex: `DB_CLAUDETE_OPS_READONLY_URL`)
- Agente nunca vê connection string
- Em logs e error messages, connection string é mascarada via regex **non-greedy**: `postgres://[^:]+:[^@\s]+@[^@\s/]+` → `postgres://<REDACTED>` (preserva o host após o `@` final, lidando com passwords contendo `@`)

## Risco

- **Alto** se mal implementado (potencial vazamento de dados de cliente).
- **Mitigação dupla**: GRANT SELECT + middleware. Falha de um não compromete.
- **Sem retorno de PII**: `describe_schema` retorna apenas metadata; `explain_query` retorna plano; `count_rows` retorna número. Nenhum retorna conteúdo de linhas.

## Implementação (repo separado)

Tecnologia: Python + `psycopg` (ou Node + `pg`). Conexão via SSL obrigatória. Connection pool com max 5.

Versão alvo: `mcp-readonly-postgres@1.0`.

## Exemplos

```json
{ "tool": "describe_schema", "args": { "db_alias": "manu_db" } }
// → schema completo sem dados

{ "tool": "explain_query", "args": { "db_alias": "manu_db", "sql": "SELECT * FROM conversations WHERE user_id = 'x'" } }
// → plano EXPLAIN

{ "tool": "explain_query", "args": { "db_alias": "manu_db", "sql": "DROP TABLE users" } }
// → erro: "Statement type not allowed: only SELECT/EXPLAIN permitted"
```
