# Git Rules

- Verificar `git status` antes de qualquer edição.
- Nunca trabalhar diretamente em `main` ou `master` sem instrução explícita.
- Branches de feature: `feature/<slug>`, infra: `infra/<slug>`, docs: `docs/<slug>`.
- Commits atômicos: uma mudança lógica por commit.
- Mensagem de commit: `<type>(<scope>): <descrição em imperativo>`.
- Nunca commitar: `.env`, secrets, credenciais, cookies, tokens.
- `git push --force`, `git reset --hard`, `git rebase`, `git cherry-pick` exigem aprovação explícita.
- PRs devem linkar Issue: `Refs #N` (parcial) ou `Closes #N` (completa).
- Não fazer merge sem aprovação explícita do Rodrigo.
