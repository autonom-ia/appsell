# Atualização do Agent Harness 2.1.2

## Proveniência

- Fonte local: `~/Documents/agent-harness`
- Fonte oficial: `https://github.com/autonom-ia/agent-harness`
- Versão: `2.1.2`
- Commit da fonte: `167e968`
- Baseline 2.0.2 comparada: `6391723`
- Issue: `#34`

## Inventário da atualização

| Componente alterado entre 2.0.2 e 2.1.2 | Disposição no AppSell |
| --- | --- |
| `platform-manifest.json` | Atualizado para 2.1.2 em todas as camadas. |
| `.autonomia-harness.json` | Marcador legado atualizado para 2.1.2. |
| `AGENTS.md` | Bloco marcado atualizado, preservando a política local mais restritiva. |
| `CLAUDE.md` | Bloco marcado atualizado, preservando a política local mais restritiva. |
| `.github/workflows/agent-harness-check.yml` | `if-no-files-found: warn` alinhado à 2.1.2. O AppSell preserva seu secret scan mais estrito: reporta somente nomes de arquivos, sem imprimir valores, e não adota a exclusão permissiva de placeholders da distribuição. |
| `.claude/rules/environment.md` | Override local preservado: staging e qualquer escrita em produção exigem aprovação. |
| `.claude/rules/git.md` | Override local preservado: merge, rebase, cherry-pick, force push e reset hard exigem aprovação. |
| `.claude/rules/prod-approval.md` | Override local preservado: merge, banco de produção, secrets, auth, billing, DNS, infraestrutura e deploy exigem aprovação. |
| `VERSION`, `BOOTSTRAP.md`, `scripts/apply-harness-to-repo.sh` | Arquivos da distribuição; não são copiados para o repositório consumidor. |

## Decisão de política local

O AppSell mantém as regras locais mais restritivas. A atualização para 2.1.2 não autoriza merge nem escrita em produção sem aprovação explícita do Rodrigo. Em conflito, prevalecem `AGENTS.md`, `CLAUDE.md` e `.claude/rules/` deste repositório.

## Validação

Executar antes da publicação da PR:

```bash
bash scripts/validate-harness.sh
bash scripts/harness-doctor.sh
git diff --check
```

O relatório do Doctor comprova saúde estrutural. Este documento complementa essa evidência com commit da fonte, baseline e overrides intencionais; o Doctor isolado não certifica equivalência byte a byte com a distribuição.

## Rollback

Reverter a futura PR da issue `#34`. Não há migration, secret, banco, deploy ou alteração de produto nesta atualização.
