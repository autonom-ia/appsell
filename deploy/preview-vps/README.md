# Autonom.ia Sell — preview isolada na VPS

Este ambiente existe apenas para o Issue #66. Ele cria recursos Docker novos, com namespace,
rótulos, rede e volumes exclusivos. Não usa serviços do Swarm, Easypanel, Traefik, DNS,
bancos, Redis, redes ou volumes existentes.

## Garantias operacionais

- a VPS nunca compila a aplicação nem autentica em registry: carrega arquivos Docker atestados pelo GitHub e executa `up --no-build`;
- imagens são construídas no GitHub Actions a partir do SHA exato do PR; cada TAR recebe artifact attestation;
- somente `127.0.0.1:13200` (Sell) e `127.0.0.1:13217` (Auth) são publicados;
- Docker Engine 28+ e gateway bridge `isolated` são gates obrigatórios;
- Domain API, Foundation, Redis e os dois PostgreSQL escutam somente no loopback do namespace;
- cada container possui limites de CPU, RAM, PIDs e rotação de logs; o preflight exige 12 GiB livres, 8 CPUs e carga baixa;
- a rede runtime é `internal` e `isolated`, sem gateway no host;
- segredos efêmeros são gerados na VPS em `preview.env` root:root `0600` e não entram no Git;
- um timer systemd exclusivo encerra e remove a preview em 48 horas, mesmo após perda da sessão SSH;
- evidências vivem em volume Docker exclusivo; não existe bind RW para diretórios do host;
- snapshots read-only preservam estado/health/restarts de containers, réplicas/nodes do Swarm,
  listeners, redes, volumes, memória e disco;
- nunca executar `docker system prune`, `docker volume prune`, `docker network prune` ou
  `docker stack rm` neste fluxo.

## Artefatos CI

Quatro TARs Docker são produzidos sem `packages: write`, login GHCR ou publicação:

- `sell-<sha>.tar` e `domain-<sha>.tar`;
- `auth-<sha>.tar`;
- `foundation-<sha>.tar`.

Antes do upload à VPS, cada TAR e o bundle de configuração devem passar `gh attestation verify`
contra o repositório de origem. Na VPS, `load-images.sh` valida checksum, tag exclusiva, image ID e
o label OCI `org.opencontainers.image.revision` contra o SHA aprovado.

## Fluxo seguro

1. Baixar e verificar os cinco artifacts CI; extrair `config` e `artifacts` em um novo
   root `/opt/autonomia-sell-preview/issue-66/<sha-curto>/`; o preflight valida `SHA256SUMS`,
   ownership root e ausência de escrita por grupo/outros.
2. Executar `snapshot-existing.sh .../snapshots/before`.
3. Executar `load-images.sh` e depois gerar `preview.env`; nenhum build ou registry login ocorre.
4. Executar `preflight.sh`; ele não cria containers, redes, volumes ou bancos.
5. Executar `up.sh`; ele agenda a expiração e executa `up --no-build`.
6. Executar `verify.sh`, que roda o E2E real no próprio namespace da VPS.
7. Executar `snapshot-existing.sh .../snapshots/after` e
   `compare-existing.sh before after`.

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
- `user.demo@autonomia.site`.

A senha é efêmera e fica apenas no `preview.env` da VPS. Como qualquer processo Docker
administrado por root, os valores Local/CI podem ser vistos por root via inspect; nenhuma
credencial de produção, Financial, Neuro.ai, GitHub OAuth ou AWS real entra no ambiente.

## Limite conhecido

Esta preview prova a jornada integrada Local/CI na infraestrutura-alvo. Não é produção,
não usa `sell.autonomia.site`, não valida Financial, Neuro.ai ou GitHub OAuth e não concede
artificialmente superadmin a `comercial@autonomia.site`. Esses gates permanecem separados
e fail-closed.
