# Autonom.ia Sell — preview isolada na VPS

Este ambiente existe apenas para o Issue #66. Ele cria recursos Docker novos, com namespace,
rótulos, rede e volumes exclusivos. Não usa serviços do Swarm, Easypanel, Traefik, DNS,
bancos, Redis, redes ou volumes existentes.

## Garantias operacionais

- a VPS nunca compila a aplicação, autentica em registry ou faz pull: carrega arquivos Docker verificados do GitHub Actions e executa `up --no-build --pull never`;
- imagens são construídas no GitHub Actions a partir do SHA exato da branch da
  Draft PR; o verificador exige run/workflow/SHA exatos, runner GitHub-hosted,
  digest SHA-256 da API e manifesto interno;
- somente `127.0.0.1:13200` (Sell) e `127.0.0.1:13217` (Auth) são publicados;
- Docker Engine 28+ e gateway bridge `isolated` são gates obrigatórios;
- Domain API, Foundation, Redis e os dois PostgreSQL escutam somente no loopback do namespace;
- cada container possui limites de CPU, RAM, PIDs e rotação de logs; o preflight exige 12 GiB livres, 8 CPUs e carga baixa;
- a rede runtime é `internal` e `isolated`, sem gateway no host;
- segredos efêmeros são gerados na VPS em `preview.env` root:root `0600` e não entram no Git;
- um timer systemd exclusivo encerra e remove a preview em 48 horas, mesmo após perda da sessão SSH; o teardown possui timeout e `kill-after` explícitos;
- evidências vivem em volume Docker exclusivo; não existe bind RW para diretórios do host;
- snapshots read-only preservam estado/health/restarts de containers, réplicas/nodes do Swarm,
  listeners, redes, volumes, memória e disco;
- nunca executar `docker system prune`, `docker volume prune`, `docker network prune` ou
  `docker stack rm` neste fluxo.

## Artefatos CI

Sete TARs Docker são produzidos sem `packages: write`, login GHCR ou publicação:

- `sell-<sha>.tar` e `domain-<sha>.tar`;
- `base-alpine-<sha>.tar`, `base-postgres-<sha>.tar` e `base-redis-<sha>.tar`;
- `auth-<sha>.tar`;
- `foundation-<sha>.tar`.

Artifact Attestations não estão disponíveis para repositórios privados pertencentes a
conta de usuário. Antes do upload à VPS, `verify-downloads-local.sh` baixa cada artifact
diretamente pelo run ID e valida, de forma fail-closed, repositório, workflow, branch,
SHA, conclusão, jobs em runners GitHub-hosted, digest SHA-256 retornado pela API do
GitHub, manifesto interno e checksum do arquivo. Na VPS, `load-images.sh` valida
checksum, tag exclusiva, image ID e o label OCI
`org.opencontainers.image.revision` contra o SHA aprovado.

## Fluxo seguro

1. Executar `verify-downloads-local.sh` com os três run IDs, os três SHAs de
   aplicação, a PR Auth e o SHA revisado do workflow Auth em `main`. O script
   baixa e verifica os oito artifacts CI e prepara `config` e `artifacts`
   para um novo root `/opt/autonomia-sell-preview/issue-66/<sha-curto>/`.
2. Transferir somente esse bundle verificado ao novo root exclusivo na VPS. O
   root remoto deve ser criado antes com `root:root 0700`; a extração como root
   usa `--no-same-owner`, preserva o namespace e não toca em nenhum caminho
   existente. Exemplo, após definir `APPSELL_SHA` e `VERIFY_ROOT`:

   ```bash
   RUN_ROOT="/opt/autonomia-sell-preview/issue-66/${APPSELL_SHA:0:12}"
   ssh root@85.31.60.100 \
     "test ! -e '$RUN_ROOT' && test ! -L '$RUN_ROOT' && install -d -o root -g root -m 0700 /opt/autonomia-sell-preview /opt/autonomia-sell-preview/issue-66 '$RUN_ROOT'"
   tar -C "$VERIFY_ROOT/upload" -cf - config artifacts \
     | ssh root@85.31.60.100 \
       "tar --no-same-owner --no-overwrite-dir -xf - -C '$RUN_ROOT'"
   ```

   Se a conexão cair, não execute a ativação: o gate de árvore/checksums recusará
   o bundle parcial. Remova somente esse novo `RUN_ROOT` incompleto após inspeção
   e repita a transferência.
3. Executar apenas `activate-preview.sh RUN_ROOT APPSELL_SHA AUTH_SHA
   FOUNDATION_SHA`. O orquestrador:
   - registra snapshot read-only antes de qualquer carga;
   - valida arquitetura, utilitários, Compose, portas, recursos e headroom antes
     do primeiro `docker image load`;
   - carrega somente as sete tags exatas, cria env efêmero e roda preflight;
   - agenda TTL, executa `up --no-build --pull never`, E2E real e snapshot/comparação final;
   - aciona teardown imediato se load, start, E2E ou comparação falhar.

`up.sh` recusa execução direta para impedir preview ativa sem o gate
pós-start. Os timeouts externos impedem bootstrap ou E2E presos até o TTL.

O rollback usa `teardown.sh`. Antes de `down --volumes`, o script valida os labels de
propriedade do Issue #66 em containers, rede e nos três volumes do projeto exato.

## Acesso do Rodrigo

Abrir um túnel SSH sem expor portas publicamente:

```bash
ssh -N \
  -L 13200:127.0.0.1:13200 \
  -L 13217:127.0.0.1:13217 \
  root@85.31.60.100
```

Depois acessar `http://localhost:13200`.

Contas da preview:

- `admin.demo@autonomia.site`;
- `user.demo@autonomia.site`;
- `comercial@autonomia.site`, com grant `platform_superadmin` restrito à inspeção
  Local/CI read-only da tenant demo.

A senha é efêmera e fica apenas no `preview.env` da VPS. Como qualquer processo Docker
administrado por root, os valores Local/CI podem ser vistos por root via inspect; nenhuma
credencial de produção, Financial, Neuro.ai, GitHub OAuth ou AWS real entra no ambiente.

## Limite conhecido

Esta preview prova a jornada integrada Local/CI na infraestrutura-alvo. Não é produção,
não usa `sell.autonomia.site` e não valida Financial, Neuro.ai ou GitHub OAuth. O grant
superadmin é criado apenas no PostgreSQL descartável, loopback-only e marcado da preview.
Auth, Foundation e Sell usam `NODE_ENV=development|test` e `APP_ENV=local|ci` como
allowlists explícitas; `NODE_ENV=production` bloqueia o reset antes de qualquer conexão. Os gates comerciais
permanecem separados e fail-closed.
