---
name: harness-self-assessment
description: Diagnóstico automático do harness instalado — versão, files por camada, hooks, memory, CI — gera health report. Opt-in enforcement via platform-manifest.
risk: Baixo
platforms: claude-code, codex, cursor
---

# Harness Self-Assessment (Option C)

## Quick path (uso rápido — durante install/update)

1. `bash scripts/harness-doctor.sh` → reporta missing files por camada.
2. Ler último report em `docs/harness-health/doctor-*.md`.
3. Se DEGRADED em Layer 1 (Core) → rodar `apply-harness-to-repo.sh` para reinstalar.
4. Se DEGRADED em Layer 2-4 → reportar ao Rodrigo; não bloquear trabalho.
5. Atualizar `platform-manifest.json` com `last_assessment_at` e `drift_check_due` (now + 30d).

## Trigger — quando usar

- Após `apply-harness-to-repo.sh` concluir (validação pós-instalação).
- Em CI (job `harness-self-assessment`), a cada PR.
- Manualmente quando agente suspeitar de drift (arquivo faltando, comportamento estranho).
- Quando `drift_check_due` em `platform-manifest.json` já passou.

## Pré-condição — verificar antes de iniciar

- [ ] Estou em repo Git com harness instalado (ou tentando instalar).
- [ ] `~/Documents/agent-harness` existe (para comparar versão local vs source) OU tenho acesso ao repo source via outro caminho.
- [ ] `jq` disponível (`which jq`).
- [ ] `docs/harness-health/` é writable (criar se não existir).

## Workflow

### Passo 1: Ler `platform-manifest.json` — extrair versão instalada por camada
```bash
installed=$(jq -r '.version // "unknown"' platform-manifest.json)
mode=$(jq -r '.enforcement_mode // "warn"' platform-manifest.json)
```

### Passo 2: Comparar com source
```bash
# Source root: env var > default
SOURCE_ROOT="${AGENT_HARNESS_SOURCE:-$HOME/Documents/agent-harness}"
if [ -f "$SOURCE_ROOT/VERSION" ]; then
  available=$(tr -d '[:space:]' < "$SOURCE_ROOT/VERSION")
else
  available="unknown"
  echo "[WARN] Source não encontrado em $SOURCE_ROOT — drift check skip"
fi
```
Se `$installed != $available` (e ambos conhecidos) → drift detectado (registrar warning).

**Em CI**: source não está disponível no runner. Drift check pula com warning explícito.

### Passo 3: Por camada, listar arquivos esperados vs encontrados
Rodar `bash scripts/harness-doctor.sh .` — output estruturado por L1/L2/L3/L4.

### Passo 4: Por skill, verificar frontmatter + seções obrigatórias
Para cada `skills/<name>/SKILL.md`:
- Frontmatter válido (name, description, risk, platforms)
- Seções: Trigger | Quando usar, Workflow | Passo, Checklist | Validação, Output, Escalação

### Passo 5: Verificar hooks
- `.claude/hooks/pre-tool-use.sh` é shell script real (shebang válido)?
- `.claude/hooks/post-tool-use.sh` idem?
- `.claude/settings.json` referencia os hooks?

### Passo 6: Verificar memory files schema
Para cada `docs/memory/MEMORY_*.md`:
- Existe?
- Tem schema (não apenas placeholder com seções vazias)?

### Passo 7: Verificar CI workflow
- `.github/workflows/agent-harness-check.yml` existe?
- Tem os 7 jobs do v2?

### Passo 8: Calcular status por camada
- **OK**: todos os arquivos esperados presentes e válidos
- **DEGRADED**: ≥1 arquivo faltando ou inválido
- **MISSING**: camada inteira ausente (ex: nenhum arquivo de Layer 3)

### Passo 9: Gerar `docs/harness-health/harness-report-YYYY-MM-DD-HHMMSS.md`
```markdown
# Harness Health Report [data]
Installed: <version> (mode=<mode>)
Available: <version>
Status geral: UP_TO_DATE | OUTDATED | DEGRADED

## Layer 1 — Core: <status>
[detalhes]

## Layer 2 — Capabilities: <status>
[detalhes]

## Layer 3 — Infra: <status>
[detalhes]

## Layer 4 — Observatory: <status>
[detalhes]

## Itens faltando
[lista por camada]

## Itens desatualizados
[lista com diff de versão]

## Próxima ação
[exata]
```

### Passo 10: Aplicar ação conforme `on_degraded` em platform-manifest.json
```bash
on_degraded_l1=$(jq -r '.on_degraded.layer_core // "WARN_CI"' platform-manifest.json)
```
Valores possíveis:
- `FAIL_CI` → exit 1 em CI
- `WARN_CI` → exit 0 com anotação `::warning::`
- `CREATE_ISSUE` → `gh issue create` com health report (e ainda exit 0)
- `SILENT` → só logar em arquivo

**Precedência (regra master):**
1. Se `enforcement_mode=warn` (default): **on_degraded é ignorado** — todo degradation vira warning. Exit 0 sempre.
2. Se `enforcement_mode=strict`: `on_degraded` por camada é aplicado conforme tabela acima.

Defaults quando `on_degraded.<layer>` ausente (e mode=strict):
- Layer 1 (Core): `FAIL_CI`
- Layer 3 (Infra): `FAIL_CI`
- Layer 2 (Capabilities): `WARN_CI`
- Layer 4 (Observatory): `WARN_CI`

Cenário CREATE_ISSUE + strict + degraded: cria Issue E exit 1 (não exclude).

### Passo 11: Atualizar `platform-manifest.json`
```bash
now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
due=$(date -u -v+30d +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "+30 days" +"%Y-%m-%dT%H:%M:%SZ")
jq --arg now "$now" --arg due "$due" '.last_assessment_at = $now | .drift_check_due = $due' platform-manifest.json > platform-manifest.json.tmp && mv platform-manifest.json.tmp platform-manifest.json
```

### Passo 12: Se executado em CI — comentar resultado na PR
```bash
gh pr comment <PR> --body "$(cat docs/harness-health/harness-report-*.md | tail -20)"
```

## Checklist de validação — hard-assert

- [ ] platform-manifest.json existe e é JSON válido
- [ ] Versão instalada vs source comparada
- [ ] Status por camada calculado (OK | DEGRADED | MISSING)
- [ ] Report gerado em `docs/harness-health/`
- [ ] Ação aplicada conforme on_degraded e enforcement_mode
- [ ] last_assessment_at + drift_check_due atualizados
- [ ] Se CI: anotação `::warning::` ou comment na PR

## Output esperado

Ao final desta skill deve existir:
- `docs/harness-health/harness-report-YYYY-MM-DD-HHMMSS.md` com status por camada
- `platform-manifest.json` com `last_assessment_at` e `drift_check_due` atualizados
- Se DEGRADED: ação executada conforme `on_degraded` config (warn, fail, create issue)
- Se em CI: comentário ou anotação na PR

## Escalação — quando parar e pedir aprovação

- `platform-manifest.json` malformado: parar — não criar manifest novo (risco de downgrade silencioso). Reportar ao Rodrigo (já tratado por `apply-harness-to-repo.sh v2`).
- Drift > 1 versão major (ex: instalado v1.0.2, available v3.0.0): escalar — pode ter breaking changes na migration.
- Layer 1 (Core) `MISSING`: harness está fundamentalmente quebrado. Escalar antes de tentar fix automático.

## Risco e rollback

Risco: Baixo (diagnóstico — não modifica estado significativo do repo).
Rollback: deletar `docs/harness-health/harness-report-*.md` se report estiver errado. Restaurar `platform-manifest.json` do backup git (`git checkout HEAD -- platform-manifest.json`).

## Exemplos

### Exemplo 1: Repo recém instalado (esperado OK)
- Installed: 2.0.0 / Available: 2.0.0 → UP_TO_DATE
- Todas as camadas OK
- Próxima ação: nenhuma. Sair com exit 0.

### Exemplo 2: Repo com v1 sendo migrado
- Installed: (none) / Detectado `.autonomia-harness.json` v1.0.2 / Available: 2.0.0 → OUTDATED
- Layer 3 (Infra) MISSING: sem hooks v2
- Próxima ação: rodar `apply-harness-to-repo.sh` para atualizar.

### Exemplo 3: Repo com Layer 2 DEGRADED
- Installed: 2.0.0 / Available: 2.0.0 → UP_TO_DATE
- Layer 2: missing skills `incident-response`, `security-review` (4 de 12 ausentes)
- Próxima ação: reinstalar (ou aceitar como warn se opção foi instalação parcial)

### Exemplo 4: CI run em PR
- Comentário automático na PR: "Harness Health: DEGRADED. Layer 2 missing 4 skills. Não bloqueia (mode=warn)."
- Anotação no log do GH Actions
- Exit 0 (warn mode)
