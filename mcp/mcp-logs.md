# mcp-logs

MCP para leitura de logs de serviços com masking automático de secrets/PII.

## Propósito

Permitir que agentes investiguem logs de produção/staging durante debug e incident response **sem expor secrets, tokens, ou PII** que possam estar em logs mal sanitizados.

## Tools

### `get_service_logs(service_name, lines?, since?)`
Últimas N linhas de log de um serviço.
- **Input:**
  - `service_name` (string): alias do serviço (ex: `manu`, `lili`, `hub2you-api`)
  - `lines` (number, default 100, max 1000)
  - `since` (string ISO8601, opcional): logs após este timestamp
- **Output:** `[{ ts, level, message_masked }]` — `message_masked` já passou pelo masking
- **Risco:** Médio (logs podem conter dados sensíveis — masking obrigatório)

### `search_logs(service_name, pattern, since?)`
Grep em logs do serviço.
- **Input:**
  - `service_name` (string)
  - `pattern` (string): regex
  - `since` (string ISO8601, opcional)
- **Output:** array de linhas que match (com masking aplicado)
- **Risco:** Médio (mesmo de get_service_logs)

### Tools NÃO disponíveis

- `restart_service` — restart via este MCP **não**. Use ops-agent + aprovação humana.
- `kill_process` — não.
- `deploy_service` — não.
- `delete_logs` — não.

## Enforcement

### Masking de secrets/PII (no MCP server, antes de retornar ao agente)

Padrões mascarados automaticamente:
```
[A-Za-z0-9_\-]{32,}           → <REDACTED>  (tokens longos)
token\s*[=:]\s*\S+             → token=<REDACTED>
key\s*[=:]\s*\S+               → key=<REDACTED>
password\s*[=:]\s*\S+          → password=<REDACTED>
Bearer\s+\S+                   → Bearer <REDACTED>
Basic\s+\S+                    → Basic <REDACTED>
postgres://[^@]+@              → postgres://<REDACTED>@
mysql://[^@]+@                 → mysql://<REDACTED>@
[\w._-]+@[\w._-]+\.\w+         → <EMAIL>     (emails — PII opcional)
```

**Princípio**: mask conservador (preferir falso positivo a vazamento).

### Audit
- Cada tool call gera linha em `docs/audit/mcp-logs.jsonl`: `{ ts, service, tool, lines_returned, masked_count }`
- Sem armazenar conteúdo dos logs lidos (apenas metadata).

### Rate limit
- Max 10 calls/min por agente para evitar grep massivo / exfiltração lenta.

## Credenciais

- Acesso ao backend de logs (Loki, CloudWatch, journald, Docker) configurado server-side via env vars.
- Agente nunca vê endpoint nem credenciais do backend.

## Risco

- **Médio**: logs podem conter dados sensíveis se aplicação loga mal.
- **Mitigação**: masking automático no MCP server (não confiar no agente para mascarar). Padrões conservadores.
- **Read-only enforçado**: sem capacidade de ação em serviço (restart, kill, deploy).

## Implementação (repo separado)

Backends suportados (configurável):
- Loki (Grafana)
- CloudWatch Logs (AWS)
- journald (systemd)
- Docker logs (via socket — sandbox)

Versão alvo: `mcp-logs@1.0`.

## Exemplos

```json
{ "tool": "get_service_logs", "args": { "service_name": "manu", "lines": 50, "since": "2026-05-25T14:00:00Z" } }
// → 50 linhas com masking aplicado

{ "tool": "search_logs", "args": { "service_name": "lili", "pattern": "429", "since": "2026-05-25T00:00:00Z" } }
// → linhas contendo "429" (rate limit) nas últimas horas, com mask
```
