# Quality Gates

O gate executável é `bash scripts/harness-ci.sh`. O mesmo entrypoint roda localmente e no job único `harness-ci`; checks extras são fases condicionais, nunca jobs adicionais.

Checks próprios do projeto são declarados em `ci.project_checks.targeted` e `ci.project_checks.full` como `{name, cwd, command[]}`. O Harness executa argv diretamente, sem `eval`.

## Perfis de risco

| Perfil | Uso | Regra de bloqueio |
|---|---|---|
| `lean` | Docs, pesquisa e mudança operacional reversível | Executa todos os checks aplicáveis; ausência local de tool fica explícita como `SKIPPED`. Gitleaks continua bloqueante quando disponível/obrigatório no CI. |
| `standard` | Default compatível para manifests novos e antigos | ShellCheck, Bats, actionlint, zizmor, Doctor e scans aplicáveis devem passar no CI. |
| `critical` | Auth, billing, produção, banco, secrets, infraestrutura ou fluxo adversarial | Mesmos gates do standard, mas tool aplicável ausente também falha. Review humano e rollback continuam obrigatórios. |

O perfil não muda autorização: merge, deploy, produção, dados, secrets, auth, billing e infraestrutura seguem os gates humanos do repositório.

## Matriz executável

| Fase | Quando roda | Comando | Saída segura |
|---|---|---|---|
| `plan` | Sempre | `bash scripts/harness-ci.sh --phase plan` | Somente flags booleanas e nome relativo do config. |
| `core` | Sempre | `bash scripts/harness-ci.sh --phase core` | Bash `-n`, ShellCheck incremental no PR/completo semanal, Bats, actionlint, zizmor offline e Doctor JSON. |
| `gitleaks` | PR/push; full semanal | `bash scripts/harness-ci.sh --phase gitleaks` | Contagem redigida; nunca valor, linha, commit ou caminho do achado. |
| `osv` | Lockfile alterado; full semanal | `bash scripts/harness-ci.sh --phase osv` | OSV em resultado agregado; relatório bruto não é retido. |
| `promptfoo` | Arquivo de prompt/agente alterado, config presente e todos os gates de live eval autorizados | `bash scripts/harness-ci.sh --phase promptfoo` | Resultado agregado; prompt/resposta não entra no summary. |
| `project` | Config do escopo contém checks | `bash scripts/harness-ci.sh --phase project --project-scope targeted|full` | Nome, duração e status; comando e output não entram no summary. |
| `summary` | Sempre, inclusive após falha | `bash scripts/harness-ci.sh --phase summary` | Versão, perfil, checks run/skipped, duração e resultado. |

## Operação local e CI

Rodar tudo localmente:

```bash
bash scripts/harness-ci.sh --phase all
```

Durante uma onda de correção, usar `--phase project --project-scope targeted`. Depois da convergência, rodar `full` uma única vez antes da revisão ampla final. O CI usa `full`.

O fallback local registra tools ausentes como `SKIPPED`. O workflow instala versões pinadas com checksum e define `HARNESS_CI_REQUIRE_TOOLS=true`; portanto, uma instalação quebrada não produz falso verde. Sucesso não cria artifact. Em falha, apenas `harness-ci-failure.txt` sanitizado pode ser retido por sete dias.

Em PR, push ou execução local baseada em diff, ShellCheck roda somente nos arquivos `.sh` rastreados que foram alterados. Sem shell alterado, registra `SKIPPED: no_changed_shell_files`. No scan semanal habilitado por `ci.weekly_full_scan`, roda sobre todo o inventário rastreado. Assim, dívida legada continua visível no semanal sem bloquear uma PR alheia, enquanto qualquer erro novo em shell permanece bloqueante no próprio diff.

O Harness não classifica providers nem carrega o config para decidir se pode executar. `promptfoo eval` só é chamado com mudança de prompt/agente, `ci.prompt_eval.live_providers=true`, `ci.prompt_eval.budget_usd > 0` no manifesto e `HARNESS_LIVE_EVAL=true`. Sem todos esses gates, a validação fica limitada à presença do arquivo, à versão pinada da CLI e a checks estruturais seguros que não carreguem nem executem o config. O budget autoriza a execução; não é um hard cap de cobrança.
