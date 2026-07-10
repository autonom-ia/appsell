# mcp-github-project

MCP para leitura e atualização de itens em GitHub Projects v3.

## Propósito

Permitir que agentes consultem e atualizem o GitHub Project `Autonom.ia Dev` (https://github.com/users/autonom-ia/projects/3) sem expor token nem precisar de scope amplo.

## Tools

### `get_project_items(project_url, filters?)`
Lista itens do Project, opcionalmente filtrados.
- **Input:**
  - `project_url` (string): URL do Project (ex: `https://github.com/users/autonom-ia/projects/3`)
  - `filters` (object, opcional): `{ status?, projeto?, tipo?, ambiente? }`
- **Output:** array de items com campos `{ id, title, url, status, projeto, tipo, prioridade, risco, ambiente, proxima_acao }`
- **Risco:** Baixo (read-only)

### `update_item_fields(item_id, fields)`
Atualiza campos de um item.
- **Input:**
  - `item_id` (string): ID do item (PVTI_...)
  - `fields` (object): `{ status?, projeto?, tipo?, prioridade?, risco?, ambiente?, proxima_acao? }`
- **Output:** `{ ok: true, updated_fields: [...] }`
- **Risco:** Médio (modifica state visível ao time)

### `add_item_to_project(issue_or_pr_url, project_url)`
Adiciona Issue/PR ao Project.
- **Input:**
  - `issue_or_pr_url` (string): URL completa da Issue ou PR
  - `project_url` (string): URL do Project
- **Output:** `{ id: "PVTI_...", url: "..." }`
- **Risco:** Baixo (add é reversível via item-delete)

### `delete_item(item_id)` — **NÃO IMPLEMENTAR sem aprovação Rodrigo**
Tool de delete só será adicionada com política explícita: requer aprovação per-call, audit log, e confirmação dupla.

## Enforcement

- **Scope GitHub token**: `project, read:project`. Token deve ser gerado com scope mínimo — sem `repo` ou `admin`.
- **Audit log**: cada tool call gera linha em `docs/audit/mcp-github-project.jsonl` no repo cliente, com `{ ts, tool, item_id, fields_changed }`.
- **Rate limit**: respeitar limite GitHub API (5000 req/h para autenticado). Implementação deve incluir backoff.

## Credenciais

- Token armazenado em variável de ambiente **no servidor MCP** (ex: `GITHUB_TOKEN`).
- Nunca expor token em log, response, ou error message.
- Token resolvido server-side — agente não vê o valor.

## Risco

- **Médio**: tools `update_item_fields` e `add_item_to_project` modificam Project visível ao time.
- **Mitigação**: campos restritos via enum (Status só aceita valores válidos do Project), update reversível.
- **Sem delete**: tool de delete não está disponível nesta v1 — exige extensão futura com política dedicada.

## Implementação (repo separado)

Tecnologia sugerida: TypeScript + GitHub GraphQL v4 (Projects v3 API). Deploy: Docker container, healthz `/health`, métricas via OpenTelemetry.

Versão alvo da spec: `mcp-github-project@1.0`.

## Exemplos

```json
// Adicionar PR e setar campos
{ "tool": "add_item_to_project", "args": { "issue_or_pr_url": "https://github.com/autonom-ia/agent-harness/pull/3", "project_url": "https://github.com/users/autonom-ia/projects/3" } }
// → { "id": "PVTI_lAHOC3T16M4BX9UHzgtt5gg", "url": "..." }

{ "tool": "update_item_fields", "args": { "item_id": "PVTI_lAHOC3T16M4BX9UHzgtt5gg", "fields": { "status": "PR aberta", "projeto": "Infra", "tipo": "Infra", "prioridade": "P1", "risco": "Baixo", "ambiente": "Dev", "proxima_acao": "Revisar PR" } } }
// → { "ok": true, "updated_fields": ["status", "projeto", "tipo", "prioridade", "risco", "ambiente", "proxima_acao"] }
```
