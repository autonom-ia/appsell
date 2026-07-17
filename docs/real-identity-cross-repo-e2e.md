# E2E real de Identity da Autonom.ia Sell

Issue: [#65](https://github.com/autonom-ia/appsell/issues/65)

Este gate sobe somente infraestrutura descartável e local. Ele prefere:

- Autonom.ia Sell em `http://localhost:3200`;
- Autonom.ia Auth em `http://127.0.0.1:3217`;
- Identity Foundation em `http://127.0.0.1:3218`;
- Domain API em `http://127.0.0.1:8100`;
- Redis e PostgreSQL em portas loopback efêmeras.

Se uma das quatro portas preferenciais já estiver ocupada, o runner não encerra o
processo existente: escolhe uma porta loopback livre e recalcula callback,
allowlists e configuração do navegador. Portas explícitas podem ser exigidas com
`E2E_REAL_SELL_PORT`, `E2E_REAL_AUTH_PORT`, `E2E_REAL_FOUNDATION_PORT` e
`E2E_REAL_DOMAIN_PORT`.

Ele usa o provider demo real do Auth, o handoff SigV4 real, os handlers e
repositories reais da Foundation e o BFF OAuth/PKCE real da Autonom.ia Sell. O
navegador valida admin demo, usuário demo, callback, cookie HttpOnly, dashboard,
biblioteca, curso Codex com cinco aulas/PDFs e ausência de credenciais provider
em superfícies legíveis.

`APPSELL_IDENTITY_DEMO_ENABLED` habilita somente o mapeamento das duas contas do
Auth para as personas locais durante este E2E. Ele não altera a landing e falha
fechado em `NODE_ENV=production`.

## Dependências locais

Por padrão, o runner usa os worktrees irmãos das Draft PRs atuais:

```text
../autonomia-identity-auth-issue-23-pr21
../autonomia-identity-foundation-issue-35
```

Os caminhos podem ser substituídos explicitamente com
`AUTONOMIA_IDENTITY_AUTH_DIR` e `AUTONOMIA_IDENTITY_FOUNDATION_DIR`.

## Execução

```bash
pnpm test:e2e:identity-real
```

O runner substitui portas preferenciais ocupadas e recusa qualquer porta
explicitamente exigida que já esteja em uso. Também recusa Redis fora do wrapper
descartável e banco fora do sentinel/capability da Foundation. Senhas, chaves
locais, credenciais SigV4 e chaves RSA
são geradas para cada execução. `SIGINT`, `SIGTERM` e saída normal encerram os
processos e removem Redis, PostgreSQL e o build temporário da Autonom.ia Sell.
As capturas aprovadas são produzidas em staging por execução e substituem a
evidência anterior somente depois de E2E e scan Redis verdes. Elas ficam em
`output/playwright/identity-real-evidence/admin-dashboard.png` e
`output/playwright/identity-real-evidence/buyer-library.png` e
`output/playwright/identity-real-evidence/buyer-first-lesson.png`.

O teste não acessa AWS, VPS, DNS, staging, produção ou bancos existentes. O nome
`appsell-web` permanece somente como chave técnica compatível do cliente OAuth.
