---
name: harness-install-or-update
description: Instalar ou atualizar o Autonom.ia Agent Harness no repo atual via apply-harness-to-repo.sh, sem perguntar opção A/B/C.
risk: Médio
platforms: claude-code, codex, cursor
---

# Harness Install or Update

## Quick path (uso rápido — repo está limpo e tem git)

1. Verificar `git status` limpo e estar em repo Git.
2. Rodar `bash ~/Documents/agent-harness/scripts/apply-harness-to-repo.sh --source ~/Documents/agent-harness --repo .`
3. Revisar diff.
4. Commit já feito pelo script + PR via `gh pr create`.
5. Atualizar Project via skill `project-update`.

## Trigger — quando usar

- Rodrigo diz: "Verifique o Autonom.ia Agent Harness neste projeto."
- Repo novo entra no portfólio Autonom.ia.
- Nova versão do harness saiu e repo está desatualizado (detectado via self-assessment).

## Pré-condição — verificar antes de iniciar

- [ ] Estou em repo Git (`git rev-parse --git-dir` retorna sem erro).
- [ ] `git status` limpo (sem mudanças pendentes).
- [ ] `~/Documents/agent-harness` existe e é fonte válida do harness.
- [ ] Tenho permissão para criar branch e abrir PR no repo.
- [ ] Sem conflito crítico previsto (ex: regra local que permite auto-merge).

## Workflow

### Passo 1: Ler BOOTSTRAP.md da fonte
`cat ~/Documents/agent-harness/BOOTSTRAP.md` — entender procedimento atual.

### Passo 2: Verificar estado do repo

```bash
git status
ls platform-manifest.json .autonomia-harness.json 2>/dev/null
```

Possíveis estados:
- Sem harness: nenhum manifesto → instalar
- v1 instalado: `.autonomia-harness.json` presente → migrar para v2
- v2 instalado: `platform-manifest.json` presente → atualizar (preserva enforcement_mode)

### Passo 3: Verificar conflitos críticos
Procurar em AGENTS.md, CLAUDE.md por regras que reduzem segurança:
- "merge automático"
- "auto-merge"
- "deploy automático em produção"
- "pode fazer merge sem aprovação"

Se encontrar: parar, escalar ao Rodrigo.

### Passo 4: Executar apply-harness-to-repo.sh
```bash
bash ~/Documents/agent-harness/scripts/apply-harness-to-repo.sh \
  --source ~/Documents/agent-harness \
  --repo .
```

O script:
- Cria branch `docs/install-agent-harness-v1` ou `docs/update-agent-harness-v1`
- Preserva regras locais via upsert markers
- Gera `platform-manifest.json` (canônico v2)
- Mantém `.autonomia-harness.json` (legacy compat)
- Faz commits atômicos

### Passo 5: Revisar diff
```bash
git diff main..HEAD --stat
git diff main..HEAD -- AGENTS.md CLAUDE.md  # arquivos sensíveis
```

Confirmar que regras locais foram preservadas (marcadores `<!-- AUTONOMIA_AGENT_HARNESS_START/END -->`).

### Passo 6: Abrir PR
```bash
gh pr create --title "feat: install/update Autonom.ia Agent Harness" \
  --body "$(cat <<'EOF'
## Sumário
Instala/atualiza o Autonom.ia Agent Harness via apply-harness-to-repo.sh.

## Test plan
- [x] Validar diff (regras locais preservadas)
- [x] CI passa (warn-mode default)
- [ ] Aprovação Rodrigo para merge

## Project update
- Status: PR aberta
- Tipo: Infra
- Prioridade: P1
- Risco: Baixo (warn-mode default)
- Ambiente: Dev
EOF
)"
```

### Passo 7: Atualizar Project
Via skill `project-update`.

### Passo 8: Self-assessment (Option C — quando F5 estiver pronto)
Rodar `bash scripts/harness-doctor.sh` para verificar instalação completa.

### Passo 9: Reportar ao Rodrigo
- Sumário do que mudou
- Link da PR
- Status do Project
- Próxima ação: revisar PR

## Checklist de validação — hard-assert

- [ ] Branch criada com prefixo `docs/install-agent-harness` ou `docs/update-agent-harness`
- [ ] Regras locais preservadas (verificado via diff em AGENTS.md/CLAUDE.md)
- [ ] `platform-manifest.json` gerado com version correto
- [ ] PR aberta linkando a Issue (`Refs #N`) se aplicável
- [ ] Project atualizado
- [ ] **Não fez merge** — aguarda Rodrigo

## Output esperado

Ao final desta skill deve existir:
- Branch nova com commits do harness
- PR aberta no GitHub
- Project atualizado
- `platform-manifest.json` no root do repo

## Escalação — quando parar e pedir aprovação

- Conflito crítico em AGENTS.md/CLAUDE.md (regra local reduz segurança).
- Manifesto malformado (script aborta com exit 3 + backup `.broken.<timestamp>`).
- Repo afeta múltiplos projetos (mono-repo cross-Manu/Lili).
- Trabalho pendente (working tree dirty) — não usar `--allow-dirty` em produção.

## Risco e rollback

Risco: Médio (modifica AGENTS.md, CLAUDE.md, .claude/, scripts, CI).
Rollback: `git checkout main && git branch -D <harness-branch>` (sem merge feito ainda). Ou `git revert` se merge já ocorreu.

## Exemplos

### Exemplo 1: Repo novo sem harness
- Estado: nenhum manifesto.
- Comando: `bash apply-harness-to-repo.sh --source ~/Documents/agent-harness --repo .`
- Resultado: branch `docs/install-agent-harness-v1`, todos os arquivos do repo-kit copiados, `platform-manifest.json` gerado com `enforcement_mode=warn`.

### Exemplo 2: Repo com v1 (legacy)
- Estado: `.autonomia-harness.json` presente.
- Resultado: branch `docs/update-agent-harness-v1`, manifesto migrado para v2, legacy preservado com `_note`.

### Exemplo 3: Repo com v2 + enforcement_mode=strict
- Estado: `platform-manifest.json` com mode=strict.
- Resultado: re-install preserva strict, atualiza version do harness apenas.
