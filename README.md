# appsell

Plataforma SaaS white-label mobile-first para produtos digitais e áreas de membros da Autonom.ia

## Documentação

- [Plano Executivo](docs/plans/EXECUTIVE_PLAN.md)
- [Plano Técnico](docs/plans/TECHNICAL_PLAN.md)
- [Relatório de Verificação](docs/research/VERIFICATION_REPORT.md)
- [Arquitetura, ADRs e threat model](docs/architecture/README.md)

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

- não há Identity externo, refresh real, HA/failover ou integração KMS real;
- o provider de keyring por env é exclusivo de Local/CI e falha fechado em
  `NODE_ENV=production`; ele prova o contrato de envelope encryption, não KMS;
- a sessão Redis contém somente identidade e metadados sanitizados; access e
  refresh tokens sintéticos aparecem apenas no vault cifrado server-side;
- o mock `/dev/identity/*` não é uma implementação de produção;
- nenhuma autorização multi-tenant/RBAC está implementada nesta etapa.

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
AppSell usado no teste. Não migrar nem limpar Redis compartilhado, staging ou
produção.
