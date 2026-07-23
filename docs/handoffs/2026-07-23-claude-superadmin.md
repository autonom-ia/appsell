# Handoff Claude — superadmin global real e desbloqueio do AppSell

Data de corte: 2026-07-23 14:25 BRT  
Estado: desenvolvimento e PRs draft; nenhuma ativação em produção  
Conta reservada: `comercial@autonomia.site`  
Conta de teste administrativo comum: `rodrigo.victor@autonomia.site`

## Resultado pretendido

`comercial@autonomia.site` deve se tornar o primeiro
`platform_superadmin` global real. O Identity prova uma passkey, o Admin decide
e persiste a autorização, e os serviços consumidores recebem snapshots
auditáveis. O Financial só reconhece `financial.admin` depois da projeção
oficial.

O objetivo não é colocar o papel em um token Cognito nem fazer INSERT manual em
tabela projetada.

## Estado executivo

| Área | Estado confirmado | Evidência |
|---|---|---|
| Desenho cross-repo | pronto em draft | AppSell PR #134, CI verde |
| Identity Foundation | implementado em draft | PR #49, `validate` verde |
| Identity Auth UI | implementada em draft | PR #41, `validate` e contrato de PR verdes |
| Review independente Identity | aprovado | bridge e UI sem findings abertos |
| Admin bootstrap explícito | não implementado | PR #12 ainda contém a base antiga |
| Outbox Admin → Financial | implementada em stack draft antiga | Admin PR #15 |
| Outbox Admin → AppSell | não implementada | somente contrato no PRD |
| Projeção Financial | implementada em stack draft antiga | Financial PRs #21 e #23 |
| Projeção AppSell | não implementada | somente contrato no PRD |
| Tela global real AppSell | não implementada | produção ainda usa fluxos normais/demo isolada |
| Grant de `comercial@...` | não executado | conta ainda não é superadmin |
| Produção | intocada nesta entrega | sem merge, deploy, banco, secret ou permissão |

## Hierarquia das fontes

1. Este handoff para o estado operacional em 2026-07-23.
2. `docs/superpowers/specs/2026-07-23-global-superadmin-design.md` para o PRD e
   invariantes arquiteturais.
3. Identity Foundation
   `docs/passkey-step-up-contract.md` para o protocolo wire.
4. `~/dev/APPSELL_96_STATUS_2026-07-22.md` para o checkout R$ 1 já publicado e
   os cuidados de produção.
5. `~/dev/APPSELL_HOTMART_PIPELINE_PLAN.md` é histórico e está supersedido nos
   pontos conflitantes.

## PRs e dependências

### Novas

- AppSell [PR #134](https://github.com/autonom-ia/appsell/pull/134) — desenho
  global. Draft, base `main`, CI verde.
- Identity Foundation
  [PR #49](https://github.com/autonom-ia/autonomia-identity-foundation/pull/49)
  — step-up server-observed. Draft, base `main`, `validate` verde.
- Identity Auth
  [PR #41](https://github.com/autonom-ia/autonomia-identity-auth/pull/41) —
  tela `/step-up/passkey`. Draft, base `main`, dois checks verdes.

### Stacks antigas que precisam de reconciliação

Admin:

```text
PR #12 feat/3-platform-rbac
  -> PR #13 feat/4-organization-scope
    -> PR #15 feat/14-financial-access-outbox
      -> PR #16 feat/7-appsell-platform-product
```

Todas continuam draft. A `main` avançou desde a criação da pilha. Antes de
alterar o bootstrap, comparar cada branch com `origin/main` e preparar uma
estratégia de atualização sem merge unilateral.

Financial:

```text
PR #21 feat/20-admin-access-projection
  -> PR #23 feat/22-access-projection-cutover
```

Ambas continuam draft. Não assumir que a projeção está em produção.

## O que a Foundation #49 entrega

- `USER_AUTH` com passkey observada pelo backend;
- sessão Cognito nunca devolvida ao navegador;
- transação e authorization code opacos, uso único e TTL máximo de 300 s;
- PKCE S256 e callback/client exatos;
- verificação dos tokens Cognito, `email_verified`, `sub`, e-mail e
  `auth_time`;
- attestation JWS PS256 com KMS assimétrica e JWKS público;
- `nonce` no claim como SHA-256 hexadecimal do nonce original;
- limpeza agendada de transações/códigos expirados;
- funções e permissões separadas por menor privilégio;
- `PASSKEY_STEP_UP_PROVISIONED` e `PASSKEY_STEP_UP_ENABLED`, ambos falsos por
  padrão;
- depois de provisionada a chave retida, rollback usa `ENABLED=false`, nunca
  desprovisiona a chave;
- contrato e runbook de rollback documentados.

Arquivos de entrada:

- `docs/passkey-step-up-contract.md`
- `docs/runbooks/passkey-step-up-rollback.md`
- `services/auth-api/src/domain/passkey-step-up/passkey-step-up.service.ts`
- `services/auth-api/src/handlers/cognito-admin-bridge.ts`
- `infra/cdk/lib/stacks/auth-api-stack.ts`
- `database/migrations/023_create_passkey_step_up.sql`

Validação mais recente:

- 19 arquivos de teste passaram e 2 suites PostgreSQL foram puladas sem URL
  descartável;
- 55 testes passaram e 6 foram pulados;
- build completo dos 5 pacotes passou;
- synth/contratos CDK passaram;
- CI remoto `validate` passou;
- Harness remoto apareceu `skipped`, portanto não chamar de Harness verde.

## O que a UI #41 entrega

- rota `/step-up/passkey`;
- nenhum fallback de senha para a operação privilegiada;
- clique explícito antes de chamar WebAuthn;
- callback HTTPS, parâmetros únicos e PKCE S256 de exatamente 43 caracteres;
- `rpId` igual ao host e `userVerification=required`;
- suporte a `smart-card` e tolerância a transport hints futuros;
- comparação exata de `state` e `redirect_uri`;
- nenhum token, sessão Cognito, attestation ou verifier PKCE no browser;
- nenhuma escrita em cookie, `localStorage` ou `sessionStorage`;
- cancelamento/timeout com mensagem segura e retry;
- JSON malformado falha fechado.

Arquivos de entrada:

- `src/app/step-up/passkey/page.tsx`
- `src/features/auth/passkey-step-up.tsx`
- `src/lib/auth/passkey-step-up.ts`
- respectivos testes;
- `vitest.config.ts` usa JSX automático, alinhado ao Next.

Validação mais recente:

- 10/10 testes passaram no CI remoto;
- build remoto com `@autonom-ia/subscription-sdk@0.1.38` passou;
- `pull-request-contract` passou;
- review Codex independente aprovou sem findings.

## Invariantes que não podem regredir

1. Identity prova autenticação e fator; Admin decide autorização.
2. O browser não declara `assurance`, `amr` ou papel.
3. `sub` da attestation, sessão AppSell e tokens enviados ao Admin devem ser
   idênticos.
4. `jti`, code e transação são single-use.
5. `GET /admin/me` é estritamente read-only.
6. A única atribuição inicial de `platform_superadmin` ocorre no endpoint
   explícito de bootstrap.
7. Rotas genéricas e organizações não podem atribuir o papel global.
8. Admin grava papel, singleton, consumo do `jti`, auditoria e duas outboxes na
   mesma transação.
9. Financial e AppSell nunca recebem PII nos snapshots.
10. Nenhuma escrita direta em `financial.admin_access_subjects` ou na futura
    `platform_admin_access_subjects`.
11. Revogação nasce no Admin e é projetada para os dois consumidores.
12. Produção não reconhece `demo-superadmin`.

## Próximo bloco exato para Claude

### 1. Reconciliar a pilha Admin

- buscar `origin/main`;
- comparar PRs #12, #13, #15 e #16 com a main atual;
- identificar o efeito colateral de bootstrap no `GET /admin/me`;
- propor a menor atualização empilhada que preserve outbox e escopo;
- não fazer merge/rebase destrutivo sem aprovação.

### 2. Implementar o bootstrap explícito no Admin

Contrato:

```text
POST /admin/platform/bootstrap
```

Exige:

- access e ID token válidos com o mesmo `sub`;
- `email_verified=true`;
- subject reservado configurado;
- e-mail exato `comercial@autonomia.site`;
- attestation PS256 válida e fresca;
- `iss`, `aud`, `client_id`, `sub`, `amr`, `auth_time`, `exp`, `jti` e
  `nonce` validados;
- `jti` ainda não consumido;
- singleton ainda não ligado a outro subject.

Transação única:

```text
admin user
  + admin.user_roles(platform_superadmin)
  + bootstrap singleton
  + consumed jti
  + audit
  + Financial snapshot outbox
  + AppSell snapshot outbox
```

O role deve conter explicitamente:

```text
appsell.platform.superadmin
financial.admin
```

### 3. Implementar Admin → AppSell

- evento `admin.platform_access.snapshot`, schema v1;
- fila e DLQ próprias;
- payload sem PII;
- revisão monotônica;
- snapshot completo, duplicata idempotente e evento antigo ignorado.

### 4. Implementar a projeção AppSell

- migration aditiva para `platform_admin_access_subjects`;
- renumerar se `0021` já estiver ocupada na main;
- somente consumidor escreve;
- `/me.platform_roles` deriva o papel exclusivamente da projeção;
- descontinuar/falhar fechado o bootstrap local;
- callback real direciona superadmin para `/platform`;
- APIs globais autorizam no servidor.

### 5. Certificar Financial

- revisar as PRs #21/#23 contra a main atual;
- provar snapshot positivo, revogação, duplicata e desordem;
- provar `GET /financial/operators` para titular e `403` para admin comum;
- nenhuma escrita manual na projeção.

## Depois do superadmin: checkout de R$ 1

Fonte: `~/dev/APPSELL_96_STATUS_2026-07-22.md`.

Estado já existente:

- produto público `autonomia/teste-checkout-r1`;
- oferta ativa e publicada por R$ 1,00;
- Appsell API/web em produção na revisão `a5ace54` conforme a fotografia de
  2026-07-22;
- migrations `0017` a `0020` aplicadas naquela fotografia.

Cuidados financeiros:

- Financial `plan_price.price` está em reais;
- usar `price: 1`, nunca `100`;
- `billingCycle` só aceita `monthly` ou `yearly`;
- smoke usa `monthly`, depois reembolsa e cancela;
- Stripe é LIVE;
- verificar novamente produção antes de confiar na fotografia de 2026-07-22.

Ordem:

1. obter `financial.admin` pela projeção;
2. criar `catalog_item`, `plan` e `plan_price`;
3. configurar mapas/secrets com backup e env completo;
4. rodar `provision_checkout`;
5. pagar R$ 1 real;
6. confirmar webhook, fulfillment e acesso;
7. reembolsar e cancelar.

## Autorizações

Não há autorização aberta para merge, deploy, produção, banco, secrets,
infraestrutura, billing ou concessão. Cada ação dessas exige novo 🟢 explícito.

Permissões antigas dadas em outras etapas não devem ser tratadas como cheque em
branco para um rollout novo.

## Riscos ainda abertos

- rate limiting/custo do endpoint público de início do step-up ainda precisa de
  decisão explícita antes de produção;
- stacks Admin/Financial estão antigas e empilhadas;
- Harness remoto foi pulado em Foundation e AppSell;
- o conector do GitHub Project #3 retornou 404; alguns campos foram registrados
  como comentário de issue, não persistidos no Project;
- não existe E2E cross-repo;
- não existe staging comprovado para a cerimônia real;
- a conta `comercial@...` não foi reconsultada ao vivo durante este handoff;
- nenhum grant foi concedido.

## Definição honesta de pronto

Só declarar `comercial@autonomia.site` superadmin quando:

- role persistido no Admin para o `sub` reservado;
- bootstrap e `jti` auditados;
- outboxes confirmadas;
- projeções AppSell e Financial confirmadas;
- `/platform` permitido para o titular;
- `/financial/operators` retorna sucesso para o titular;
- admin comum continua negado;
- janela de bootstrap fechada;
- evidências e rollback registrados.
