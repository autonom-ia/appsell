# Autonom.ia Sell

Plataforma SaaS white-label mobile-first para produtos digitais e áreas de membros da Autonom.ia

## Documentação

- [Plano Executivo](docs/plans/EXECUTIVE_PLAN.md)
- [Plano Técnico](docs/plans/TECHNICAL_PLAN.md)
- [Relatório de Verificação](docs/research/VERIFICATION_REPORT.md)
- [Arquitetura, ADRs e threat model](docs/architecture/README.md)
- [OpenBao Transit Local/CI](docs/openbao-transit-local-ci.md)

## Spike OAuth/BFF local

O spike demonstra OAuth Authorization Code + PKCE com callback no BFF e sessão
opaca. As rotas usam somente Redis, com TTL e consumo atômico; não existe
fallback para memória. O mock de Identity continua exclusivamente local e
recusa execução quando `NODE_ENV=production`.

```bash
corepack pnpm install
cp .env.example .env.local
# Ajuste para um Redis NOVO/isolado; não use a porta 6379 ou instância existente.
corepack pnpm dev
```

Abra `http://localhost:3000/auth/login`. O fluxo termina em `/dashboard`, sem
code ou token na URL final. O browser recebe apenas cookies opacos `HttpOnly`.

Validação:

```bash
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
corepack pnpm test:e2e
```

`APPSELL_REDIS_URL`, `APPSELL_REDIS_PREFIX`,
`APPSELL_SESSION_HMAC_KEY_B64` e
`APPSELL_VAULT_LOCAL_KEYRING_B64_JSON` são obrigatórios. Gere duas chaves locais
distintas com `openssl rand -base64 32`: a primeira deriva o ID de sessão via
HMAC; a segunda envolve uma DEK aleatória por registro do vault. A chave HMAC
não pode ser reutilizada como chave do vault. O prefixo deve
seguir `appsell:<ambiente>:<instância-única>`. IDs opacos só aparecem nas chaves
como SHA-256; transaction, code, sessão e vault expiram por TTL do Redis. O
payload de tokens sintéticos fica sob AES-256-GCM com AAD vinculada a namespace,
sessão, subject e revision; sessão e vault são criados e removidos atomicamente. A suíte de
integração e o E2E criam um `redis-server` efêmero em porta aleatória diferente
de 6379, protegido por senha aleatória. O harness valida o PID do processo antes
de testar e aguarda seu encerramento antes de remover o diretório temporário.

Limites deliberados do spike:

- o adapter Identity e o OpenBao Transit são reais em Local/CI, mas ainda não
  existe certificação de staging, HA/failover ou workload identity;
- ambos os providers de vault falham fechados em `NODE_ENV=production` neste
  slice; o keyring por env continua sendo somente uma compatibilidade Local/CI;
- a sessão Redis contém somente identidade e metadados sanitizados; access e
  refresh tokens sintéticos aparecem apenas no vault cifrado server-side;
- o mock `/dev/identity/*` não é uma implementação de produção;
- a autorização seller/RBAC, buyer entitlement e assets privados existem no
  domínio Local/CI; checkout, cobrança e Identity externo continuam ausentes.

### Rotação do keyring local

O keyring local existe apenas para testes Local/CI e não substitui KMS:

1. adicionar a nova chave como `active: false`, mantendo a antiga ativa;
2. validar que todas as instâncias conseguem ler envelopes antigos;
3. promover exatamente uma chave nova para `active: true`, mantendo antigas
   read-only;
4. inventariar `kid` e executar rewrap/read-repair antes de remover qualquer
   chave antiga — essa automação ainda não existe e está bloqueada na Issue #19.

Remover uma chave com envelopes ainda ativos faz a leitura falhar fechada. A KEK
em variável de ambiente também não protege contra RCE no BFF; o gate real exige
KMS/secret manager, IAM mínimo e staging isolado. As chaves relacionadas por Lua
usam uma hash tag Redis derivada do namespace `appsell:<ambiente>:<instância>`:
ficam juntas por instância sem concentrar todas as instâncias no mesmo slot.
HA/cluster ainda não foi certificado.

## Fundação PostgreSQL/API local

A API de domínio usa FastAPI e PostgreSQL 16 com três roles separadas:
`appsell_owner` (sem login), `appsell_migrator` e `appsell_runtime`. O runtime
não é owner e não possui `SUPERUSER` ou `BYPASSRLS`. A migration inicial força
RLS e valida um contexto transacional assinado antes de liberar dados do seller.

O teste abaixo cria senhas/chave efêmeras, escolhe uma porta livre, sobe um
PostgreSQL exclusivo, aplica a migration do zero, tenta acesso cross-tenant e
falsificação de contexto e remove contêiner e volume ao terminar:

```bash
uv sync --project apps/api --frozen
./scripts/test-postgres-foundation.sh
```

Para desenvolvimento manual, copie `.env.example`, substitua todos os valores
`REPLACE_*` por segredos locais diferentes e execute a migration antes da API:

```bash
docker compose up -d --wait postgres
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
uv run --project apps/api python -m appsell_api.configure_context_key
docker compose up -d --build api
```

`APPSELL_CONTEXT_SIGNING_KEY_HEX` deve conter 32 bytes aleatórios em hexadecimal
(`openssl rand -hex 32`). O comando é idempotente para a mesma chave e falha
fechado se uma chave diferente já estiver configurada; ele nunca imprime o
segredo. `APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX` é uma segunda chave, exclusiva
para atestar os claims fortes do bootstrap e nunca pode reutilizar a chave do
contexto comum. As três variáveis `APPSELL_BOOTSTRAP_EXPECTED_*` fixam issuer,
audience e client ID que a atestação assinada de bootstrap precisa apresentar.
O bootstrap fica desabilitado por padrão, só aceita `APPSELL_BOOTSTRAP_ENABLED=true`
na janela controlada e se desabilita no banco após o primeiro sucesso. Uma policy
diferente também falha fechada; rotação exige fluxo próprio e não é feita
implicitamente no startup.

Esta fundação ainda não concede o superadmin nem integra o Identity externo. O
bootstrap one-shot exige claims reais verificadas e MFA/passkey; não deve ser
simulado em produção.

### Tenant e contas demo Local/CI

O modo demo cria uma tenant dedicada e resetável com duas personas fixas, sem
senha hardcoded: `admin.demo@autonomia.site` e `user.demo@autonomia.site`. O
comprador recebe somente o produto publicado **Codex na prática**, com cinco
PDFs protegidos pelo BFF; o admin não herda o entitlement do comprador. O BFF
nega ao comprador demo todas as capabilities de seller/catálogo antes da chamada
server-to-server, expira os cookies CSRF de produtor sem entregar tokens úteis e
redireciona acesso direto a `/products` para `/library`. O admin demo pode listar
somente seu contexto e usar capabilities de catálogo apenas no UUID fixo da
tenant demo; onboarding de outra tenant e seller IDs diferentes falham em 403.

Depois de configurar o PostgreSQL/API local conforme a seção anterior, habilite
o modo somente no ambiente Local/CI, marque explicitamente esse banco local
descartável e execute o reset:

```bash
docker compose exec -T postgres psql -U postgres -d appsell \
  -c "comment on database appsell is 'appsell-demo-reset-target-v1'"

APPSELL_DEMO_ENABLED=true \
NODE_ENV=development \
APP_ENV=local \
APPSELL_API_ENVIRONMENT=local \
APPSELL_API_IDENTITY_ISSUER="$APPSELL_IDENTITY_ISSUER" \
APPSELL_DEMO_DATABASE_URL="postgresql://postgres:${APPSELL_POSTGRES_SUPERUSER_PASSWORD}@127.0.0.1:${APPSELL_POSTGRES_PORT:-55432}/appsell" \
uv run --project apps/api python -m appsell_api.reset_demo_tenant \
  --confirm-demo-key autonomia-demo-v1

NODE_ENV=development APP_ENV=local APPSELL_DEMO_ENABLED=true corepack pnpm dev
```

Abra `http://localhost:3000` e escolha **Entrar como admin demo** ou **Entrar
como aluno demo**. O reset usa advisory lock, IDs determinísticos e valida hash
e tamanho dos cinco PDFs antes de alterar a tenant. O comando recusa ambiente
fora de Local/CI, DSN fora de loopback ou banco sem o marcador explícito; o
modo demo também recusa redirectors libpq de rede/serviço (`PGHOST*`, `PGPORT`,
`PGDATABASE`, `PGSERVICE*`, `PGSYSCONFDIR`) e `NODE_ENV=production`.

#### Inspeção superadmin da demo

O fluxo `/platform/demo-inspection` é uma projeção temporária, read-only e
auditada da tenant demo. Ele não impersona o seller, não cria membership e não
expõe PII de compradores, conteúdo privado, billing, assets ou secrets. O
seller inspecionado é fixado no servidor; formulário, query string e cookie não
podem selecionar outra tenant. Cada ticket tem TTL máximo de 15 minutos, guarda
somente o SHA-256 do token no PostgreSQL e exige revogação explícita ou expiração.

A conta sintética `comercial@autonomia.site` permanece oculta da landing e só
existe quando `NODE_ENV` está em `development|test`, `APP_ENV` está em
`local|ci`, o modo demo correspondente está ativo e
`APPSELL_DEMO_SUPERADMIN_ENABLED=true`. Essas flags **não concedem** o papel: o
callback ainda exige um `platform_superadmin` ativo vinculado ao issuer + `sub`.
Auth, Foundation e Sell usam allowlists positivas Local/CI; valores ausentes,
`preview`, `staging` ou `production` falham fechado. O e-mail nunca é usado como
autorização permanente e a identidade demo não existe no provider AWS real.

Os PDFs ficam fora do web root e são autorizados novamente por
`entitlement_id + asset_id` antes de cada stream. Em Local/CI, a raiz privada
padrão é `output/pdf/codex`; em produção, configure obrigatoriamente um diretório
absoluto em `APPSELL_PRIVATE_LEARNING_ASSET_ROOT`. O processo falha fechado se a
raiz, o arquivo regular, o tamanho ou o SHA-256 não coincidirem com os metadados
autorizados pelo domínio.

Para ver a jornada completa em infraestrutura local descartável:

```bash
./scripts/start-local-demo-preview.sh 3200
```

Abra `http://localhost:3200` e use os botões de admin ou aluno demo. O script
cria PostgreSQL, API e Redis isolados; `Ctrl+C` remove containers, volume,
segredos efêmeros e arquivos gerados. Ele não acessa VPS, DNS ou produção.

### Ponte BFF→API e onboarding seller Local/CI

O BFF sincroniza a identidade local na API e assina cada chamada interna com um
envelope ligado a método, path, query, hash do corpo, capability, sessão,
identidade, issuer/audience, `kid` e TTL. A API valida a credencial de serviço,
consome o nonce de replay no PostgreSQL e instala contexto RLS assinado apenas
dentro da transação.

Após o login local, a tela Produtos permite criar uma organização seller. O
POST público exige origem canônica e token CSRF derivado da sessão e da ação;
o corpo é lido em streaming com teto de 16 KiB. A criação é idempotente e
atômica e concede `seller_owner` ao criador. O teste de fundação cobre o fluxo
real com dois BFFs, Redis, FastAPI e PostgreSQL:

```bash
./scripts/test-postgres-foundation.sh
```

### Edição, preview e publicação Local/CI

Em **Produtos**, escolha o tenant explicitamente e abra **Continuar edição**.
O editor persiste produto, curso e oferta informativa com optimistic locking,
monta uma prévia privada e publica um snapshot imutável. Publicar não cria
checkout, cobrança, purchase ou entitlement; Financial permanece desconectado.

As mutações exigem Origin exata, CSRF separado por ação e corpo limitado. Retry
exato de publicação devolve o receipt original; snapshot idêntico com nova
intenção falha fechado. Conflitos de versão preservam os campos locais e usam o
fluxo **Recarregar versão do servidor**.

Este fluxo não transforma a identidade sintética em conta real. Em particular,
`comercial@autonomia.site` só poderá ser superadmin real após integração e
prova do Identity externo com issuer/JWKS/audience/client/MFA válidos.

### Refresh distribuído Local/CI

O contrato server-side de refresh coordena instâncias independentes por Redis,
sem criar rota HTTP e sem expor tokens ao browser. A implementação usa lease
curto, fence decimal exato, intent idempotente por revision e commit Lua com
compare-and-swap. O intent transita por `prepared`, `inflight`, `retryable` ou
`ambiguous`: só `transient` comprovadamente sem side effect libera retry; crash
após `inflight` bloqueia nova chamada. O commit preserva o menor TTL do par sessão/vault e nunca
ultrapassa `session.expiresAt` ou a expiração do refresh token novo. Logout e
`invalid_grant` removem também as chaves de coordenação. `invalid_grant` exige
revision/fence atuais; logout é uma revogação explícita e apaga o namespace da
sessão incondicionalmente para impedir ressurreição.

O provider injetado é somente um contrato de teste. Resultado upstream
ambíguo fica persistido e bloqueia retry automático: lease/fencing não consegue
provar se um Identity externo rotacionou o token antes de perder a resposta.
Idempotência real, política de rotação e recuperação dependem do contrato e do
ambiente isolado da Issue #13; portanto, refresh real continua bloqueado.

A versão 2 do envelope inclui `revision` na AAD e `tokenRevision` na sessão,
tornando envelopes
anteriores incompatíveis de propósito. Como este slice é apenas Local/CI,
upgrade ou rollback exige apagar somente o namespace Redis descartável do
Autonom.ia Sell usado no teste. Não migrar nem limpar Redis compartilhado, staging ou
produção.
