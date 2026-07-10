# mcp-docs

MCP para busca em docs internos (Notion, Confluence, filesystem local).

## Propósito

Permitir que agentes encontrem rapidamente documentos internos (runbooks, ADRs, specs, knowledge base) sem trocar de ferramenta. Read-only.

## Tools

### `search_docs(query, source?)`
Busca full-text em docs internos.
- **Input:**
  - `query` (string): texto de busca
  - `source` (enum, opcional): `notion | confluence | local | all` — default `all`
- **Output:** `[{ title, path_or_url, source, snippet }]`
- **Risco:** Baixo (read-only)

### `get_doc(path_or_url)`
Lê conteúdo de um documento específico.
- **Input:**
  - `path_or_url` (string): path local ou URL Notion/Confluence
- **Output:** `{ title, content_markdown, metadata: { author, last_modified } }`
- **Risco:** Baixo

### Tools NÃO disponíveis

- `create_doc` — escrita via MCP **não**. Criar doc é decisão deliberada — use ferramenta nativa.
- `update_doc` — não.
- `delete_doc` — não.

## Enforcement

### Sources permitidas (whitelist)
- **local filesystem**: apenas `docs/`, `docs/runbooks/`, `docs/adr/`, `docs/superpowers/specs/`, `docs/superpowers/plans/` — fora dessas pastas, retorna erro
- **Notion**: workspace `autonom-ia` apenas; respeitar permissões nativas Notion (sem bypass)
- **Confluence**: instance `autonom-ia.atlassian.net` apenas

### Audit
- Cada tool call em `docs/audit/mcp-docs.jsonl`: `{ ts, source, tool, query_hash, results_count }`
- Sem armazenar conteúdo retornado (apenas metadata).

### PII / secret check no output
- Mesmo masking de `mcp-logs` aplicado ao conteúdo retornado, defensivamente (caso doc interno tenha credencial em texto).

## Credenciais

- API tokens (Notion, Confluence) resolvidos server-side via env vars.
- Agente nunca vê credenciais.
- Token Notion: scope leitura apenas (sem write/admin).
- Token Confluence: scope leitura apenas.

## Risco

- **Baixo**: read-only com sources whitelisted.
- Risco residual: doc interno pode conter secret/PII se escrito mal. Mitigação: masking defensivo no output.

## Implementação (repo separado)

Backends:
- Notion: oficial API v1
- Confluence: REST API v2
- Local: filesystem walk com `glob` em pastas permitidas

Indexação opcional via SQLite FTS para query rápida em local (não obrigatório v1).

Versão alvo: `mcp-docs@1.0`.

## Exemplos

```json
{ "tool": "search_docs", "args": { "query": "rollback Manu Easypanel", "source": "all" } }
// → lista de docs com snippet

{ "tool": "get_doc", "args": { "path_or_url": "docs/runbooks/manu-rollback.md" } }
// → conteúdo markdown + metadata
```
