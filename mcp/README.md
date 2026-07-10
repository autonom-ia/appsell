# MCP — Model Context Protocol specs

Specs (contratos) de MCPs usados pelos harness Autonom.ia. **Implementação dos MCPs vai em repo separado** — esta pasta contém apenas a definição de contrato (tools, enforcement, credenciais, risco).

## MCPs definidos

| MCP | Propósito | Risco | Status |
|-----|-----------|:-----:|:------:|
| `mcp-github-project` | Read/write GitHub Projects v3 (Status, Tipo, Prioridade, etc.) | Médio | Spec definida |
| `mcp-readonly-postgres` | Introspecção segura de banco (schema, EXPLAIN, count) | Alto | Spec definida |
| `mcp-logs` | Leitura de logs de serviços com masking de secrets | Médio | Spec definida |
| `mcp-docs` | Busca em docs internos (Notion, Confluence, local) | Baixo | Spec definida |

## Regra geral

MCPs com produção ou dados sensíveis exigem:
1. Política explícita de segurança (RBAC, scope mínimo, secret resolution server-side)
2. Audit log de cada tool call
3. Masking de secrets/PII no output
4. Tools read-only por padrão; write tools exigem aprovação explícita

## Implementação

MCPs são implementados em repo separado conforme as specs aqui. Ver issue/repo `autonom-ia/mcp-implementations` (a criar).

Cada implementação deve:
- Validar contra a spec deste diretório
- Versionar (`mcp-<name>@<version>`)
- Documentar deployment (Docker, env vars, healthz)
- Incluir testes de integração contra fixtures
