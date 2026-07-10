---
name: project-update
description: Atualizar GitHub Project Autonom.ia Dev com 7 campos obrigatórios via gh API.
risk: Baixo
platforms: claude-code, codex, cursor
---

# Project Update

## Quick path (uso rápido — apenas se Project já tem item criado)

1. Identificar Issue/PR ativa.
2. `gh project item-add 3 --owner autonom-ia --url <url>` se não estiver no Project.
3. Atualizar 6 single-selects + 1 text via `gh project item-edit`.
4. Validar com `gh project item-list 3 --owner autonom-ia --format json | jq`.

## Trigger — quando usar

- Ao abrir Issue.
- Ao abrir PR.
- Ao mudar status de tarefa (Em desenvolvimento → PR aberta → Em review → Mergeada).
- Ao concluir fase de épico.
- Quando Rodrigo perguntar status do Project.

## Pré-condição — verificar antes de iniciar

- [ ] `gh auth status` mostra account `autonom-ia` autenticado.
- [ ] Tenho scope `project, read:project`. Se faltar: `gh auth refresh -s project,read:project`.
- [ ] Sei qual Issue/PR atualizar.
- [ ] Sei os valores para os 7 campos.

## Workflow

### Passo 1: Identificar Project ID

```bash
gh project list --owner autonom-ia
# Procurar "Autonom.ia Dev" — ID retornado (ex: PVT_kwHOC3T16M4BX9UH)
```

Project number: `3`. Owner: `autonom-ia`.

### Passo 2: Adicionar Item ao Project (se não estiver)

```bash
gh project item-add 3 --owner autonom-ia --url https://github.com/<org>/<repo>/pull/<N>
```

### Passo 3: Obter item ID

```bash
gh project item-list 3 --owner autonom-ia --format json --limit 100 \
  | jq '.items[] | select(.content.url == "<url>") | .id'
```

### Passo 4: Obter field IDs e option IDs

```bash
gh project field-list 3 --owner autonom-ia
# Para opções dos single-selects, usar GraphQL:
gh api graphql -f query='
query {
  node(id: "<PROJECT_ID>") {
    ... on ProjectV2 {
      fields(first: 50) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id name
            options { id name }
          }
        }
      }
    }
  }
}'
```

### Passo 5: Atualizar 7 campos obrigatórios

```bash
PROJECT_ID="PVT_kwHOC3T16M4BX9UH"
ITEM_ID="<from passo 3>"

# 1. Status (single-select)
gh project item-edit --id $ITEM_ID --project-id $PROJECT_ID \
  --field-id <STATUS_FIELD_ID> --single-select-option-id <OPTION_ID>

# 2. Projeto (single-select: Claudete | Studio | Lili | Manu | Hub2You | Sócio Odonto | Assist Card | Infra)
# 3. Tipo (single-select: Bug | Feature | Infra | Docs | Refactor | Segurança)
# 4. Prioridade (single-select: P0 | P1 | P2 | P3)
# 5. Risco (single-select: Baixo | Médio | Alto)
# 6. Ambiente (single-select: Local | Dev | Staging | Produção)
# 7. Próxima ação (text)
gh project item-edit --id $ITEM_ID --project-id $PROJECT_ID \
  --field-id <NEXT_ACTION_FIELD_ID> --text "<próxima ação exata>"
```

### Passo 6: Validar

```bash
gh project item-list 3 --owner autonom-ia --format json --limit 100 \
  | jq '.items[] | select(.id == "<ITEM_ID>")'
# Confirmar que todos os 7 campos estão preenchidos
```

### Passo 7: Se falhar (sem permissão ao Project)

Adicionar seção na PR/Issue:
```markdown
## Project update pendente

- Projeto:
- Status:
- Tipo:
- Prioridade:
- Risco:
- Próxima ação:
- Ambiente:
```

Não criar `PROJECT_UPDATE_PENDENTE.md` na raiz por padrão. Apenas no corpo da PR/Issue.

## Checklist de validação — hard-assert

- [ ] Item está no Project
- [ ] Status correto para fase atual (PR aberta, Em review, Aprovada, Mergeada)
- [ ] Projeto identificado corretamente (não default "Infra" se for feature de Manu)
- [ ] Tipo, Prioridade, Risco, Ambiente preenchidos
- [ ] Próxima ação é exata (não "continuar" — deve ser ação acionável)
- [ ] Validação visual via `gh project item-list`

## Output esperado

Ao final desta skill deve existir:
- Item no Project com 7 campos preenchidos
- Histórico no Project mostra mudança de status

## Escalação — quando parar e pedir aprovação

- gh sem scope `project,read:project`: pedir Rodrigo rodar `gh auth refresh -s project,read:project`.
- Campo de Projeto incerto (PR cross-project): perguntar antes de chutar.
- Próxima ação ambígua: parar — definir antes de marcar status.

## Risco e rollback

Risco: Baixo (update reversível via API).
Rollback: re-rodar `gh project item-edit` com valores anteriores.

## Exemplos

### Exemplo 1: Abertura de PR
- Status: `PR aberta`
- Projeto: `Infra` (harness)
- Tipo: `Infra`
- Prioridade: `P1`
- Risco: `Baixo`
- Ambiente: `Dev`
- Próxima ação: `Revisar PR de harness v2.0 F1`

### Exemplo 2: PR merged
- Status: `Mergeada`
- Próxima ação: `Validar instalação do harness v2 em sandbox`

### Exemplo 3: Sem acesso ao Project
- Adicionar na PR: seção `## Project update pendente` com 7 campos.
- Reportar ao Rodrigo: "Sem scope project — `gh auth refresh -s project,read:project` necessário."
