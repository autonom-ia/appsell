# Environment-Aware Rules

## Detectar ambiente
1. Variável `APP_ENV` ou `NODE_ENV` ou `RAILS_ENV`
2. Arquivo `.autonomia-env` na raiz do repo (conteúdo: `dev` | `staging` | `production`)
3. URL do serviço (`.onrender.com`, `.railway.app` → staging; domínio próprio → produção)
4. Nome do repo (sufixo `-prod`, `-production` → produção)
5. Default quando incerto: tratar como produção.

## Por ambiente
- `dev / local`: pode editar, commitar, testar, abrir PR sem aprovação adicional.
- `staging`: pode ler e testar livremente; deploy requer aprovação.
- `production`: toda escrita (env, DB, config, deploy, restart) requer aprovação explícita antes de executar. Fazer backup antes de qualquer PATCH.
