# Spec executável — Convites de membros (Bloco 3)

- Status: Draft para revisão (não implementado)
- Issue: https://github.com/autonom-ia/appsell/issues/95 (Part of #92)
- Data: 2026-07-17
- Escopo: emissor autorizado cria convite de uso único → convidado loga na Identity → callback resolve o convite → cria `identity_user` (se novo) + membership/grant/entitlement + audit.
- Fora de escopo: transferência de ownership (fluxo próprio com aceite do novo owner), bootstrap de superadmin (Bloco 1), UI final (só contrato).
- Molde de idempotência reutilizado: `app_private.onboard_seller` (`apps/api/migrations/versions/0002_bff_api_onboarding.py:178`) e a rota `POST /v1/me/sellers` (`apps/api/src/appsell_api/routes.py:186`).

## 0. Racional e âncoras no código existente

O convite é um **fluxo pré-grant**: uma identidade autenticada que ainda não é membro
daquele tenant (ou ainda nem existe em `identity_user`) executa uma transação única e
delimitada que a promove a um papel específico, sem criar privilégio global implícito.
É exatamente a categoria descrita em `docs/architecture/AUTHORIZATION_MATRIX.md`
(seção "Fluxos pré-grant") ao lado de `Bootstrap inicial` e `Criar tenant próprio`.

Portanto o consumo do convite herda o mesmo padrão já implementado e testado do
onboarding de seller:

| Propriedade | onboard_seller (existente) | consume_invitation (proposto) |
|---|---|---|
| Capability dedicada validada no contexto | `seller.onboard` (`0002:194`) | `invitation.consume` |
| `seller_organization_id` do envelope | exige `NULL` (`0002:196`, `routes.py:230`) | exige `NULL`; o tenant vem da linha do convite, nunca do browser |
| Idempotência por chave + corpo | `app_private.seller_onboarding_request` (`0002:77`) | `app_private.invitation_consume_request` |
| Advisory lock por chave | `pg_advisory_xact_lock(idempotency_key)` (`0002:209`) | `pg_advisory_xact_lock(idempotency_key)` |
| Advisory lock por invariante de negócio | dono único por identidade (`0002:221`) | uso único do token (CAS) |
| Efeitos atômicos | org + membership + request + audit | membership/grant/entitlement + request + audit |
| Erros mapeados | `23505→409`, `22023→422`, `28000/42501→403` (`routes.py:257`) | idênticos, + `410 Gone` para token expirado/revogado |

O envelope interno já carrega tudo que o contexto precisa: `identity` (`issuer`, `sub`,
`user_id`), `capability`, `seller_organization_id`, `request_id`, `iat/exp`
(`internal_requests.py:261` monta o `apply_request_context`). A autorização de linha
continua no Postgres via `app_private.context_uuid/context_text`
(`0001_domain_foundation.py:124`/`:144`) e via RLS por capability
(`0002:262`/`:280`/`:290`).

## 1. Modelo de dados

Migration nova `0003_membership_invitations.py` (forward-only, expand; segue o padrão
`raise RuntimeError("AppSell migrations are forward-only")` no `downgrade`, como
`0001:488` e `0002:470`).

### 1.1 Tabela `invitation`

```sql
create table invitation (
  id uuid primary key default gen_random_uuid(),

  -- Segredo em repouso: SÓ o hash. O token bruto nunca é persistido nem logado.
  token_sha256 text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),

  scope text not null check (scope in ('seller','buyer','platform')),
  target_role text not null check (target_role in (
    'seller_admin','seller_editor','seller_support',  -- scope=seller
    'buyer',                                           -- scope=buyer
    'platform_support','platform_auditor'              -- scope=platform
  )),

  -- Tenant alvo: obrigatório para seller/buyer, NULL para platform.
  seller_organization_id uuid references seller_organization(id),

  -- Coerência scope <-> tenant <-> papel:
  constraint invitation_scope_tenant_ck check (
    (scope = 'platform') = (seller_organization_id is null)
  ),
  constraint invitation_scope_role_ck check (
    (scope = 'seller'   and target_role in ('seller_admin','seller_editor','seller_support'))
    or (scope = 'buyer'    and target_role = 'buyer')
    or (scope = 'platform' and target_role in ('platform_support','platform_auditor'))
  ),

  -- Buyer: produto opcional para entitlement (materialização coordenada com Fase 5).
  target_product_id uuid,

  -- Dica de destinatário: só display/telemetria. NUNCA é chave de autorização
  -- (e-mail é atributo mutável, conforme TECHNICAL_PLAN §4).
  invited_email_hint text
    check (invited_email_hint is null
           or (invited_email_hint = lower(btrim(invited_email_hint))
               and position('@' in invited_email_hint) > 1
               and length(invited_email_hint) between 3 and 320
               and invited_email_hint !~ '[[:cntrl:]]')),

  created_by_identity_user_id uuid not null references identity_user(id),

  status text not null check (status in ('pending','consumed','revoked')),
  expires_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),

  consumed_at timestamptz,
  consumed_by_identity_user_id uuid references identity_user(id),
  revoked_at timestamptz,
  revoked_by_identity_user_id uuid references identity_user(id),

  -- Resultado do consumo, para replay idempotente devolver o mesmo efeito.
  resulting_seller_organization_id uuid references seller_organization(id),

  -- Consistência de estado (mesmo padrão do platform_grant, 0001:186):
  constraint invitation_consumed_ck check ((status = 'consumed') = (consumed_at is not null)),
  constraint invitation_consumed_actor_ck check ((consumed_at is null) = (consumed_by_identity_user_id is null)),
  constraint invitation_revoked_ck check ((status = 'revoked') = (revoked_at is not null)),
  constraint invitation_revoked_actor_ck check ((revoked_at is null) = (revoked_by_identity_user_id is null)),

  -- FK composta garante que o tenant do resultado é o mesmo do convite
  -- (impede promoção cross-tenant, invariante TECHNICAL_PLAN §5).
  constraint invitation_result_tenant_ck
    check (resulting_seller_organization_id is null
           or resulting_seller_organization_id = seller_organization_id),

  -- TTL mínimo/máximo saneado na função emissora, não no schema.
  constraint invitation_ttl_ck check (expires_at > created_at)
);

-- Um convite pendente por (tenant, papel, e-mail sugerido): evita spam/duplicidade,
-- espelhando a filosofia de unique composta do domínio (0001).
create unique index invitation_pending_seller_dedup_idx
  on invitation (seller_organization_id, target_role, invited_email_hint)
  where status = 'pending' and scope in ('seller','buyer') and invited_email_hint is not null;

create index invitation_tenant_status_idx
  on invitation (seller_organization_id, status, created_at desc);
create index invitation_expiry_sweep_idx
  on invitation (expires_at) where status = 'pending';
```

Notas de modelo:

- **`platform_superadmin` nunca é papel de convite.** Só entra por bootstrap
  (`consume_superadmin_bootstrap`) ou por grant global com step-up + dupla aprovação
  (matriz linha "Conceder/revogar papel global"). O `check` de `target_role` já o exclui.
- **`seller_owner` nunca é papel de convite.** Ownership muda por transferência com
  aceite explícito do novo owner (matriz "Transferir ownership") — não por link.
- **`buyer`** entra como scope para não fragmentar o modelo, mas a materialização do
  entitlement fica atrás de PR coordenada com Fase 5; até lá o convite `buyer` pode ficar
  desabilitado por flag na função emissora. O contrato de tabela já suporta.
- Não há coluna de token em claro em nenhum lugar. Não há coluna que aceite o token via
  trigger/log. `token_sha256` é a única representação em repouso.

### 1.2 Idempotência do consumo — `app_private.invitation_consume_request`

Espelha `app_private.seller_onboarding_request` (`0002:77`):

```sql
create table app_private.invitation_consume_request (
  idempotency_key uuid primary key,
  identity_user_id uuid not null references public.identity_user(id),
  invitation_id uuid not null references public.invitation(id),
  body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp(),
  unique (invitation_id)  -- reforça uso único também na camada de idempotência
);
revoke all on table app_private.invitation_consume_request from public, appsell_runtime;
```

## 2. Fluxo end-to-end

```text
1. Emissor autorizado (SO/SA no tenant, ou PSA na plataforma) chama a rota de criação.
2. BFF gera token bruto opaco de alta entropia (>=32 bytes, base64url) SERVER-SIDE.
   - persiste apenas sha256(token) via app_private.create_*_invitation;
   - devolve ao emissor o link contendo o token no FRAGMENTO da URL:
       https://sell.autonomia.site/invite#t=<token>
     (fragmento não vai em querystring, log de acesso, nem header Referer).
3. Convidado abre o link -> a página lê o token do fragmento no cliente.
4. Se não autenticado: inicia OAuth Code+PKCE na Identity (fluxo já existente do BFF,
   TECHNICAL_PLAN §4 "Sessão"); o token do convite é preservado client-side (não no
   servidor) durante o round-trip e reenviado ao BFF same-origin após o callback.
5. No callback, com sessão estabelecida, o BFF:
   a. sincroniza a identidade: PUT /v1/me (sync_identity, cria identity_user se novo);
   b. resolve preview: POST /v1/invitations/preview {token} -> projeção mínima;
   c. consome: POST /v1/invitations/consume {token, idempotency_key}.
6. consume_invitation (SECURITY DEFINER) executa atomicamente:
   - valida capability = 'invitation.consume' e envelope.seller = NULL;
   - advisory lock por idempotency_key -> se replay igual, devolve o mesmo resultado;
   - CAS de uso único no token (update ... where status='pending' and expires_at>now);
   - cria membership/grant/entitlement no tenant DA LINHA do convite;
   - grava invitation_consume_request + audit_event;
   - retorna o efeito (tenant, papel).
7. BFF redireciona o convidado ao shell do papel concedido.
```

Reuso explícito do molde `onboard_seller`: passos 6 replicam a sequência
`capability-check → advisory lock por idempotency → checar request existente →
advisory lock por invariante → efeitos → audit → return` de `0002:194-256`.

### 2.1 Geração e transporte do token (invariante de segurança)

- Token bruto = `base64url(os.urandom(32))` (256 bits), gerado no BFF.
- Persistência: `sha256(token)` em hex minúsculo. Comparação de consulta por hash.
- Transporte ao convidado: fragmento (`#t=`), nunca path/query. Racional: fragmento não
  é enviado ao servidor em requisições nem vaza em `Referer`/access log.
- O BFF encaminha o token à API **no corpo** (JSON), nunca em URL. A API loga apenas
  `request_id` e `invitation_id` resolvido — nunca o token nem o hash.

## 3. Autorização — quem convida o quê

Derivado de `docs/architecture/AUTHORIZATION_MATRIX.md`, seção "Memberships e
compradores" (linha "Convidar/remover seller admin/editor/support": `SO=A`, `SA=C`,
demais `N`) e "Plataforma e tenants" (linha "Conceder/revogar papel global": `PSA=C`
com step-up + dupla aprovação).

| Emissor | Pode convidar | Não pode convidar | Condição |
|---|---|---|---|
| `seller_owner` (SO) | `seller_admin`, `seller_editor`, `seller_support`, `buyer` | `seller_owner`, papéis de plataforma | membership ativa no tenant do convite |
| `seller_admin` (SA) | `seller_editor`, `seller_support`, `buyer` | `seller_admin` (poder igual), `seller_owner` (superior), plataforma | membership ativa; "poder igual/superior proibido" (matriz) |
| `seller_editor` (SE) | — | tudo | `N` |
| `seller_support` (SS) | — | tudo | `N` (import/entitlement é outro fluxo) |
| `platform_superadmin` (PSA) | `platform_support`, `platform_auditor` | `platform_superadmin`, papéis de seller | step-up (assurance mfa/passkey) + dupla aprovação registrada |
| `platform_support` (PS) / `platform_auditor` (PA) | — | tudo | `N` |
| `buyer` (B) / visitante (V) | — | tudo | `N` |

Enforcement: a validação de "quem pode o quê" acontece **dentro da função emissora**
(`SECURITY DEFINER`), consultando `seller_membership`/`platform_grant` do
`context_uuid('identity_user_id')`, jamais confiando em papel vindo do browser. Falha
fechada (`28000`/`403`) quando a condição não é provável — regra 7 da matriz.

Regra anti-escalação (seller): a função rejeita `target_role` cujo poder seja
`>=` ao do emissor quando o emissor é `seller_admin` (só `editor`/`support`/`buyer`),
e sempre rejeita `seller_owner`.

## 4. Rotas propostas e contratos

Prefixo `/v1` (mesmo `APIRouter` de `routes.py:15`). Toda rota é interna BFF→API,
autenticada por `x-appsell-service-credential` + envelope assinado
(`internal_requests.py:114`). Capabilities novas entre colchetes.

### 4.1 Emissor — seller

**`POST /v1/sellers/{sellerId}/invitations`** — cria convite de membership.
Capability `[seller.invitation.create]`. Envelope: `seller_organization_id = sellerId`.

Request:
```json
{
  "idempotency_key": "1f0a7b2c-....-uuid",
  "target_role": "seller_editor",
  "invited_email_hint": "pessoa@exemplo.com",
  "ttl_seconds": 604800
}
```
Validações (Pydantic, molde `SellerOnboardingBody`, `routes.py:38`): `idempotency_key`
UUID canônico; `target_role` no enum permitido para o scope seller; `invited_email_hint`
opcional normalizado; `ttl_seconds` inteiro em faixa `[3600, 1209600]` (1h–14d).

Response `201`:
```json
{
  "id": "uuid-do-convite",
  "invite_url": "https://sell.autonomia.site/invite#t=<token-bruto-uma-vez>",
  "target_role": "seller_editor",
  "expires_at": "2026-07-24T12:00:00Z"
}
```
O `invite_url` (com token) é retornado **uma única vez**, na criação. Nunca é
recuperável depois (só o hash fica em repouso).

Erros: `403` sem membership/role suficiente ou tentativa de escalar papel; `409`
`invitation_conflict` (replay de `idempotency_key` com corpo divergente, ou dedup de
convite pendente); `422` corpo inválido.

**`GET /v1/sellers/{sellerId}/invitations`** — lista convites do tenant.
Capability `[seller.invitation.list]`. Envelope: `seller_organization_id = sellerId`.
Response `200`: lista de `{id, scope, target_role, invited_email_hint, status,
expires_at, created_at, consumed_at}`. **Nunca** retorna `token_sha256` nem token.

**`POST /v1/sellers/{sellerId}/invitations/{invitationId}/revoke`** — revoga pendente.
Capability `[seller.invitation.revoke]`. Envelope: `seller_organization_id = sellerId`.
CAS: `update invitation set status='revoked', revoked_at=..., revoked_by=... where
id=:id and seller_organization_id=:tenant and status='pending'`. `200` `{"status":
"revoked"}`; `409` se já consumido/revogado; `404` se não pertence ao tenant.

### 4.2 Convidado — preview e consumo

**`POST /v1/invitations/preview`** — projeção mínima do convite (pós-login).
Capability `[invitation.preview]`. Envelope: `seller_organization_id = NULL`. Token no
corpo, nunca em URL.

Request: `{"token": "<token-bruto>"}` → a API calcula o hash e consulta por
`token_sha256`.

Response `200` (projeção mínima, sem PII do emissor):
```json
{
  "scope": "seller",
  "target_role": "seller_editor",
  "seller_display_name": "Produtor Exemplo",
  "status": "pending",
  "expires_at": "2026-07-24T12:00:00Z"
}
```
`404 invitation_not_found` para hash inexistente; `410 invitation_expired` /
`410 invitation_revoked` conforme estado (resposta uniforme para não vazar timing).

**`POST /v1/invitations/consume`** — consome o convite e aplica o efeito.
Capability `[invitation.consume]`. Envelope: `seller_organization_id = NULL`
(pré-grant, como `onboard_seller`). A identidade já foi sincronizada por `PUT /v1/me`.

Request:
```json
{ "token": "<token-bruto>", "idempotency_key": "uuid" }
```
Response `200`:
```json
{
  "invitation_id": "uuid",
  "scope": "seller",
  "target_role": "seller_editor",
  "seller_organization_id": "uuid-tenant"
}
```
Erros: `410 invitation_expired`/`invitation_revoked`; `409 invitation_already_consumed`
(consumido por outra identidade) ou `invitation_idempotency_conflict` (mesma chave,
corpo diferente); `422` corpo inválido; `403` se o contexto/capability não confere.

### 4.3 Emissor — plataforma (sequenciado por último, gated)

**`POST /v1/platform/invitations`** — convida `platform_support`/`platform_auditor`.
Capability `[platform.invitation.create]`. Envelope: `seller_organization_id = NULL`.
Requer que o contexto tenha `platform_superadmin` ativo **e** um registro de
segunda aprovação + assurance step-up (mfa/passkey). Enquanto o fluxo de dupla aprovação
não existir, esta rota permanece atrás de flag desabilitada (a tabela e a função já
suportam). Contrato análogo ao 4.1, sem `seller_organization_id`.

## 5. Invariantes de segurança

1. **Token nunca em log/URL/telemetria.** Só trafega no corpo BFF↔API e no fragmento do
   link. Logs registram `request_id` + `invitation_id` resolvido. Sanitização defensiva:
   qualquer string `[A-Za-z0-9_\-]{32,}` em mensagem de erro vira `<REDACTED>`.
2. **Hash em repouso.** `token_sha256` é a única forma persistida; coluna com
   `check` de formato hex-64. Impossível recuperar o token a partir do banco.
3. **Uso único por CAS.** O consumo é `update ... where status='pending' and
   expires_at > clock_timestamp()` com `pg_advisory_xact_lock` no hash; duas requisições
   concorrentes resultam em exatamente um `consumed` e um `409`/`410`.
4. **Expiração.** `expires_at` obrigatório; consumo/preview tratam `expires_at <= now`
   como `410`. Sweeper opcional marca `pending` vencidos, mas a decisão de autorização
   nunca depende do sweeper — sempre revalida `expires_at` na transação.
5. **Revogação.** CAS `pending → revoked`; convite revogado nunca consome.
6. **Sem escalada de privilégio.** `target_role` limitado por scope no schema; emissor
   `seller_admin` não concede `admin`/`owner`; `platform_superadmin` nunca é alvo de
   convite. Falha fechada.
7. **Tenant vem da linha, não do browser.** `consume` exige `envelope.seller = NULL`
   e deriva o tenant de `invitation.seller_organization_id`. FK composta impede resultado
   cross-tenant.
8. **Pré-grant isolado.** O consumo não concede privilégio global implícito; cria só a
   membership/grant/entitlement descrita na linha, dentro de transação própria
   (matriz "Fluxos pré-grant").
9. **Enforcement em camadas** (matriz "Enforcement"): BFF (sessão+CSRF+origin) → API
   (capability+objeto) → função `SECURITY DEFINER` (papel do emissor) → RLS/constraints
   (tenant/uso único). Teste negativo obrigatório em cada camada.
10. **Capabilities registradas em RLS.** As policies de leitura por capability
    (`identity_user_self_select` `0002:262`, `membership_self_select` `0002:280`,
    `seller_org_member_select` `0002:290`) devem incluir as novas capabilities de convite
    onde a leitura correspondente for necessária, e **apenas** essas. Nova policy de
    `invitation` por capability, análoga às de `audit_event` (`0001:321`/`:335`):
    `select`/`insert`/`update` restritos a `appsell_runtime` com checagem de tenant e
    capability; `appsell_owner` mantém policy `all`.

## 6. Critérios de aceite testáveis (hard-assert, por rota)

Cada critério é um teste de integração com Postgres real (padrão do repo: migrations em
banco vazio + fluxo). Zero soft pass; timeout = FAIL.

### `POST /v1/sellers/{sellerId}/invitations`
- AC-C1: SO cria convite `seller_editor` → `201`, `invite_url` presente, linha
  `invitation` com `status='pending'` e `token_sha256` de 64 hex; **assert** que o token
  bruto não aparece em nenhuma coluna nem em `audit_event`.
- AC-C2: SA cria `seller_support` → `201`. SA tenta criar `seller_admin` → `403`
  (poder igual). SA tenta `seller_owner` → `403`.
- AC-C3: SE/SS/buyer/visitante chamando a rota → `403`.
- AC-C4: replay do mesmo `idempotency_key` com mesmo corpo → mesmo `invitation.id`,
  sem segundo insert; com corpo diferente → `409`.
- AC-C5: segundo convite `pending` para mesmo (tenant, papel, e-mail) → `409` dedup.
- AC-C6: `ttl_seconds` fora de `[3600,1209600]` → `422`.
- AC-C7: emissor de outro tenant (envelope.seller = outro) → `403`/RLS nega.

### `GET /v1/sellers/{sellerId}/invitations`
- AC-L1: SO/SA listam só convites do próprio tenant; **assert** ausência de
  `token_sha256`/token no payload.
- AC-L2: identidade sem membership no tenant → `403`.

### `POST /v1/sellers/{sellerId}/invitations/{id}/revoke`
- AC-R1: SO revoga `pending` → `200`, `status='revoked'`, `revoked_by` = emissor.
- AC-R2: revogar convite já `consumed` → `409`; revogar de outro tenant → `404`.
- AC-R3: após revogar, `consume` do mesmo token → `410 invitation_revoked`.

### `POST /v1/invitations/preview`
- AC-P1: token válido → projeção mínima correta; **assert** ausência de PII do emissor
  e de qualquer hash/token.
- AC-P2: token inexistente → `404`; expirado → `410`; revogado → `410`.
- AC-P3: token em querystring (não no corpo) → rota não aceita (o contrato só lê corpo).

### `POST /v1/invitations/consume`
- AC-X1: convidado novo consome `seller_editor` → cria `identity_user` (via `PUT /v1/me`
  anterior) + `seller_membership(role=seller_editor, status=active)` no tenant da linha;
  `invitation.status='consumed'`, `consumed_by` correto, `audit_event` gravado.
- AC-X2: **uso único** — duas chamadas concorrentes com o mesmo token → exatamente um
  `200` e um `409 invitation_already_consumed`; exatamente uma membership criada.
- AC-X3: replay idempotente (mesmo `idempotency_key`, mesmo corpo, mesma identidade) →
  mesmo resultado, sem segunda membership.
- AC-X4: token expirado → `410`; revogado → `410`.
- AC-X5: envelope com `seller_organization_id != NULL` → `403` (pré-grant viola contexto,
  espelha `_require_no_seller` `routes.py:230` e `0002:196`).
- AC-X6: convidado que já é membro ativo do tenant no papel alvo → tratamento idempotente
  definido (retorna o efeito existente sem duplicar; ou `409` conforme decisão registrada).
- AC-X7: consumo cross-tenant impossível — o tenant do efeito é sempre
  `invitation.seller_organization_id`; tentativa de forçar outro tenant falha por
  função + FK composta.

### `POST /v1/platform/invitations` (gated)
- AC-PL1: com flag desabilitada → `404`/`403` determinístico.
- AC-PL2: (quando habilitado) sem dupla aprovação/step-up → `403`; PSA com ambos →
  cria convite `platform_auditor`; alvo `platform_superadmin` → `403`.

## 7. Plano de PRs pequenas

Cada PR: DRAFT, `Refs #95` (`Part of #92`), migrations forward-only, testes negativos por
camada, sem merge sem OK do Rodrigo. Reviewer + tester obrigatórios antes de "ready"
(regras 4/9 globais).

1. **PR-1 · Migration `0003_membership_invitations`** — tabela `invitation`,
   `app_private.invitation_consume_request`, funções `create_seller_invitation`,
   `consume_invitation`, `revoke_seller_invitation`, novas policies RLS por capability,
   grants a `appsell_runtime`, `schema_ready()` estendida. Testes: migração em banco
   vazio e vindo de `0002`; constraints e CAS via SQL direto.
2. **PR-2 · Rotas emissor seller** — `POST/GET /v1/sellers/{sellerId}/invitations`,
   `POST .../revoke`, Pydantic bodies, mapeamento de erro (molde `routes.py:257`).
   Testes: AC-C*, AC-L*, AC-R*.
3. **PR-3 · Resolução no callback** — `POST /v1/invitations/preview` e
   `POST /v1/invitations/consume`, integração com `PUT /v1/me` no callback BFF.
   Testes: AC-P*, AC-X*.
4. **PR-4 · Emissor plataforma (gated)** — `POST /v1/platform/invitations` atrás de flag,
   dupla aprovação + step-up. Testes: AC-PL*. Depende do fluxo de grants globais (#95
   checkbox "Grants platform_support/platform_auditor auditados").
5. **PR-5 · UI** — tela de emissão (produtor), listagem/revogação e página `/invite`
   (lê fragmento, dispara login, consome no callback). Testes E2E mobile das jornadas.

Sequência obrigatória: PR-1 → PR-2 → PR-3 → (PR-4 gated) → PR-5, cada uma atrás do gate
da anterior (regra 7 do §7 do TECHNICAL_PLAN).

## 8. Dúvidas abertas para decisão do Rodrigo

1. **Convite `buyer` agora ou só na Fase 5?** O contrato de tabela já suporta; a
   materialização do entitlement depende do modelo de `Entitlement`/`CatalogProduct`
   (ainda não migrado). Recomendação: manter scope `buyer` no schema, função atrás de
   flag, materializar em PR da Fase 5.
2. **AC-X6 (convidado já é membro):** idempotente-retorna-efeito vs `409`. Recomendação:
   retornar o efeito existente e marcar o convite `consumed` (evita link "quebrado" para
   quem já entrou), registrando em audit.
3. **Rota de revoke:** `POST .../revoke` (proposto) vs `DELETE`. Recomendação: `POST`,
   por ser transição de estado auditável, não remoção física.
