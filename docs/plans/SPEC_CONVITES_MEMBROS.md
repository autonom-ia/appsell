# Spec executável — Convites de membros (Bloco 3)

- Status: Draft para revisão (não implementado)
- Issue: https://github.com/autonom-ia/appsell/issues/95 (Part of #92)
- Data: 2026-07-17 (rev. 2 — correções do review codex do PR #101)
- Escopo (MVP): emissor autorizado cria convite de uso único → convidado loga na
  Identity → callback resolve o convite → cria `identity_user` (se novo) +
  `seller_membership` + audit. **Papéis do MVP: `seller_admin`, `seller_editor`,
  `seller_support`.** Convite de plataforma (`platform_support`/`platform_auditor`)
  fica sequenciado por último e atrás de gate (§4.3).
- Fora de escopo do MVP:
  - **Convite `buyer` → Fase 5.** Não entra no schema nem nas rotas deste bloco.
    A materialização de entitlement de buyer depende do modelo real de
    `buyer_entitlement` (que **já existe** — ver §8) e de decisão de negócio em
    aberto (D-027); detalhado na §8.
  - Transferência de ownership (fluxo próprio com aceite do novo owner).
  - Bootstrap de superadmin (Bloco 1).
  - UI final e **envio de e-mail** (D-029 decidida em #355; o envio depende
    do canal transacional da #356 — ver §5 e §7).
- Molde de idempotência reutilizado: `app_private.onboard_seller`
  (`apps/api/migrations/versions/0002_bff_api_onboarding.py:178`) e a rota
  `POST /v1/me/sellers` (`apps/api/src/appsell_api/routes.py:186`).

## 0. Racional e âncoras no código existente

O convite é um **fluxo pré-grant**: uma identidade autenticada que ainda não é
membro daquele tenant (ou ainda nem existe em `identity_user`) executa uma
transação única e delimitada que a promove a um papel específico, sem criar
privilégio global implícito.

> **Mudança de baseline (requer aprovação do Rodrigo).**
> `docs/architecture/AUTHORIZATION_MATRIX.md` está marcada como
> "Approved baseline with gates" e é deny-by-default. Sua seção
> "Fluxos pré-grant" (`AUTHORIZATION_MATRIX.md:41-50`) lista **apenas**
> `Bootstrap inicial` e `Criar tenant próprio`. **Consumir convite NÃO está lá.**
> Esta spec **propõe adicionar** uma terceira linha pré-grant
> ("Consumir convite de membership") à matriz — não a trata como já-derivada.
> A adição é uma alteração da baseline aprovada e depende do OK explícito do
> Rodrigo antes de PR-1 (ver §8).

Uma vez aprovada essa linha, o consumo do convite herda o mesmo padrão já
implementado e testado do onboarding de seller:

| Propriedade | onboard_seller (existente) | consume_invitation (proposto) |
|---|---|---|
| Capability dedicada validada no contexto | `seller.onboard` (`0002:194`) | `invitation.consume` |
| `seller_organization_id` do envelope | exige `NULL` (`0002:196`); o guard `_require_no_seller` rejeita com **401** (`routes.py:230-233`) | exige `NULL`; mesmo guard/401; o tenant vem da linha do convite, nunca do browser |
| Idempotência por chave + corpo | `app_private.seller_onboarding_request` (`0002:77`) | `app_private.invitation_consume_request` (§1.2) |
| Advisory lock por chave | `pg_advisory_xact_lock(hashtextextended(idempotency_key))` (`0002:209`) | idem |
| Advisory lock por invariante de negócio | dono único por identidade (`0002:221`) | uso único do token (CAS sobre `token_sha256`) |
| Efeitos atômicos | org + membership + request + audit | membership + request + audit |
| Erros mapeados | `23505→409`, `22023→422`, `28000/42501→403` (`routes.py:257-267`) | os mesmos, **mais** um SQLSTATE de aplicação `P0410`→`410 Gone` (token expirado/revogado), adicionado no mapeamento do router de convites (§4.2) |

O envelope interno carrega o que o contexto do Postgres precisa. Atenção ao que
`_apply_context` (`internal_requests.py:261-286`) **de fato** injeta via
`app_private.apply_request_context`: apenas `identity_user_id`,
`seller_organization_id`, `capability` e a janela `iat/exp` (mais a assinatura
HMAC). `issuer`/`sub`/`request_id` existem no envelope, **mas não entram nessa
chamada de contexto** — assim como em `onboard_seller`, o `request_id` é passado
como **argumento da função** (`p_request_id`) para o `audit_event`, não pelo
contexto. A autorização de linha continua no Postgres via
`app_private.context_uuid/context_text` (`0001_domain_foundation.py:124`/`:144`)
e via RLS por capability (`0001:296-306`, `0002:262`).

## 1. Modelo de dados

Migration nova **`0018_membership_invitations.py`** (forward-only, expand). O head
atual da cadeia Alembic é `0017_pdf_preraster` (`revision = "0017_pdf_preraster"`),
então:

```python
revision = "0018_membership_invitations"
down_revision = "0017_pdf_preraster"
```

`downgrade()` segue o padrão forward-only do repo:
`raise RuntimeError("AppSell migrations are forward-only")` (como `0001:488` e
`0002:470`). **Não** propor `0003_*` — colidiria com
`0003_product_course_drafts.py`, que já existe.

### 1.1 Tabela `invitation`

```sql
create table invitation (
  id uuid primary key default gen_random_uuid(),

  -- Segredo em repouso: SÓ o hash. O token bruto nunca é persistido nem logado.
  -- O BFF calcula sha256(token) e envia apenas o hash na criação (ver §2.1);
  -- o token bruto nunca cruza a fronteira BFF->API na emissão.
  token_sha256 text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),

  -- MVP: scope seller/platform. scope 'buyer' entra só na Fase 5 (ver §8),
  -- junto com a coluna de produto-alvo referenciando a FK composta de publication.
  scope text not null check (scope in ('seller','platform')),
  target_role text not null check (target_role in (
    'seller_admin','seller_editor','seller_support',  -- scope=seller
    'platform_support','platform_auditor'             -- scope=platform
  )),

  -- Tenant alvo: obrigatório para seller, NULL para platform.
  seller_organization_id uuid references seller_organization(id),

  -- Coerência scope <-> tenant <-> papel:
  constraint invitation_scope_tenant_ck check (
    (scope = 'platform') = (seller_organization_id is null)
  ),
  constraint invitation_scope_role_ck check (
    (scope = 'seller'   and target_role in ('seller_admin','seller_editor','seller_support'))
    or (scope = 'platform' and target_role in ('platform_support','platform_auditor'))
  ),

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

  -- Tenant do resultado do consumo, para replay idempotente devolver o mesmo efeito.
  resulting_seller_organization_id uuid references seller_organization(id),

  -- Consistência de estado (mesmo padrão do platform_grant, 0001:186):
  constraint invitation_consumed_ck check ((status = 'consumed') = (consumed_at is not null)),
  constraint invitation_consumed_actor_ck check ((consumed_at is null) = (consumed_by_identity_user_id is null)),
  constraint invitation_revoked_ck check ((status = 'revoked') = (revoked_at is not null)),
  constraint invitation_revoked_actor_ck check ((revoked_at is null) = (revoked_by_identity_user_id is null)),

  -- CHECK intra-linha (NÃO é FK): garante que, quando houver resultado, o tenant
  -- do efeito é o mesmo do convite (impede promoção cross-tenant). É uma
  -- invariante de linha; NÃO substitui a lógica da função SECURITY DEFINER, que
  -- é quem popula 'resulting_seller_organization_id' corretamente.
  constraint invitation_result_tenant_ck
    check (resulting_seller_organization_id is null
           or resulting_seller_organization_id = seller_organization_id),

  -- TTL mínimo/máximo saneado na função emissora; o schema só exige > created_at.
  constraint invitation_ttl_ck check (expires_at > created_at)
);

-- Um convite pendente por (tenant, papel, e-mail sugerido): evita spam/duplicidade,
-- espelhando a filosofia de unique composta do domínio (0001).
create unique index invitation_pending_seller_dedup_idx
  on invitation (seller_organization_id, target_role, invited_email_hint)
  where status = 'pending' and scope = 'seller' and invited_email_hint is not null;

create index invitation_tenant_status_idx
  on invitation (seller_organization_id, status, created_at desc);
create index invitation_expiry_sweep_idx
  on invitation (expires_at) where status = 'pending';
```

Notas de modelo:

- **`platform_superadmin` nunca é papel de convite.** Só entra por bootstrap
  (`consume_superadmin_bootstrap`) ou por grant global com step-up + dupla aprovação
  (matriz linha "Conceder/revogar papel global", `AUTHORIZATION_MATRIX.md:60`). O
  `check` de `target_role` já o exclui.
- **`seller_owner` nunca é papel de convite.** Ownership muda por transferência com
  aceite explícito do novo owner (matriz "Transferir ownership", `:73`) — não por link.
- **`buyer` fica fora do MVP.** Não há `scope='buyer'` nem coluna de produto-alvo
  neste bloco. A Fase 5 adicionará `scope='buyer'` e uma coluna
  `target_publication_id` com **FK composta** `(seller_organization_id,
  catalog_product_id, target_publication_id) → publication(...)`, coerente com o
  que `buyer_entitlement` já exige hoje (`publication_id NOT NULL` +
  `buyer_entitlement_publication_fkey`, `0007_learning_progress.py:184-188`). Ver §8.
- Não há coluna de token em claro em lugar nenhum. `token_sha256` é a única
  representação em repouso.

RLS (obrigatório — **todas** as tabelas de domínio têm, ex.: `0001:281-306`,
`0006:138-145`):

```sql
alter table invitation enable row level security;
alter table invitation force row level security;

-- appsell_owner: acesso total (mesmo padrão de todas as tabelas).
create policy invitation_owner_all on invitation to appsell_owner
  using (true) with check (true);

-- Leitura por membro autorizado do tenant, gated por capability de convite.
create policy invitation_tenant_select on invitation for select to appsell_runtime
  using (
    scope = 'seller'
    and seller_organization_id = app_private.context_uuid('seller_organization_id')
    and app_private.context_text('capability') in (
      'seller.invitation.list', 'seller.invitation.create', 'seller.invitation.revoke'
    )
    and exists (
      select 1 from seller_membership membership
      where membership.seller_organization_id = invitation.seller_organization_id
        and membership.identity_user_id = app_private.context_uuid('identity_user_id')
        and membership.status = 'active'
        and membership.role in ('seller_owner','seller_admin')
    )
  );
```

As **escritas** (`insert`/`update` de `invitation` e do request de idempotência)
ocorrem exclusivamente dentro das funções `SECURITY DEFINER`
(`create_seller_invitation`, `consume_invitation`, `revoke_seller_invitation`),
executando como `appsell_owner`. `appsell_runtime` recebe **apenas** `select`
(via policy acima) e **execute** nas funções; `insert/update/delete` diretos em
`invitation` ficam revogados de `appsell_runtime` (padrão de
`0006:290-291`). O consumo/preview (§4.2) usa capability `invitation.consume` /
`invitation.preview` com envelope `seller = NULL`, então a leitura do convite
pelo convidado acontece **dentro** da função SECURITY DEFINER, não por policy de
tenant.

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

### 1.3 Idempotência da criação — `app_private.invitation_create_request`

**Correção do achado #4 do review.** A criação precisa do mesmo molde de
idempotência do `onboard_seller`, senão um retry gera um token novo cujo hash não
bate com o já persistido, inutilizando o link.

```sql
create table app_private.invitation_create_request (
  idempotency_key uuid primary key,
  identity_user_id uuid not null references public.identity_user(id),
  invitation_id uuid not null references public.invitation(id),
  body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp(),
  unique (invitation_id)
);
revoke all on table app_private.invitation_create_request from public, appsell_runtime;
```

Regra de retry seguro (detalhada em §2 e §4.1):

O ponto crítico é que o token precisa ser **reproduzível entre processos** — não
pode depender de estado só em memória de uma instância do BFF. Se a API commitar e
a resposta se perder (timeout, restart do BFF), um retry de **outra instância** com
a **mesma `idempotency_key`** tem de regenerar exatamente o mesmo token e remontar
o mesmo `invite_url`.

1. **Token derivado, não aleatório-efêmero.** O token bruto é uma KDF determinística
   de um segredo do servidor com a `idempotency_key`:
   `token = base64url(HMAC-SHA256(INVITE_TOKEN_KEY, idempotency_key))` (256 bits de
   saída). `INVITE_TOKEN_KEY` é um secret dedicado (gerido como os demais secrets;
   nunca em log/commit). Propriedades:
   - **Reprodutível:** qualquer instância do BFF, a qualquer momento, recomputa o
     **mesmo** token para a mesma `idempotency_key` — sobrevive a restart/perda de
     resposta. Resolve o achado #4 (retry após "commit + resposta perdida").
   - **Imprevisível ao atacante:** como HMAC com chave secreta é um PRF, a saída é
     indistinguível de aleatória para quem não tem `INVITE_TOKEN_KEY`. A
     `idempotency_key` é um UUID gerado server-side pelo BFF (não vem do browser),
     nunca logada ao lado do token.
2. O BFF calcula `token` (passo 1) e envia à API apenas `token_sha256` (o hash),
   nunca o token bruto, no corpo junto com `idempotency_key`, `target_role`,
   `invited_email_hint`, `ttl_seconds`. `body_sha256` (calculado pelo authenticator
   sobre o corpo) **inclui** `token_sha256`.
3. A função `create_seller_invitation`:
   - `pg_advisory_xact_lock(idempotency_key)`;
   - se já existe request com **mesma** `idempotency_key` e **mesmo** `body_sha256`
     → devolve o `invitation_id` original (cujo `token_sha256` é idêntico ao token
     que o BFF acabou de **recomputar** deterministicamente) → o BFF remonta o
     **mesmo** `invite_url`: replay seguro, link válido, mesmo após restart;
   - mesma `idempotency_key` com `body_sha256` diferente → `23505` → `409`;
   - caso novo → insere `invitation` (com o `token_sha256`) + `invitation_create_request`
     + `audit_event`.
4. **Edge de rotação de `INVITE_TOKEN_KEY`.** Convites já entregues continuam válidos
   (o consumo compara o token do link contra o `token_sha256` persistido, sem
   depender da chave). Só um retry-em-voo cuja `idempotency_key` foi criada **antes**
   da rotação recomputaria um token divergente → `409`; nesse caso raro, o operador
   revoga e recria. Falha fechada (link morto, nunca exposto).

## 2. Fluxo end-to-end

```text
1. Emissor autorizado (SO/SA no tenant, ou PSA na plataforma) chama a rota de criação
   com uma idempotency_key.
2. BFF DERIVA o token deterministicamente da idempotency_key + secret do servidor:
   token = base64url(HMAC-SHA256(INVITE_TOKEN_KEY, idempotency_key)) (256 bits).
   Qualquer instância recomputa o MESMO token para a mesma idempotency_key (sobrevive
   a restart/perda de resposta).
   - calcula sha256(token) e envia à API SOMENTE o hash (token bruto nunca cruza
     a fronteira BFF->API na emissão);
   - a API persiste o token_sha256 via app_private.create_seller_invitation;
   - o BFF devolve ao emissor o link contendo o token no FRAGMENTO da URL:
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
   - cria seller_membership no tenant DA LINHA do convite;
   - grava invitation_consume_request + audit_event;
   - retorna o efeito (tenant, papel).
7. BFF redireciona o convidado ao shell do papel concedido.
```

Reuso explícito do molde `onboard_seller`: o passo 6 replica a sequência
`capability-check → advisory lock por idempotency → checar request existente →
advisory lock por invariante → efeitos → audit → return` de `0002:194-256`. A
criação (passo 2) usa o mesmo molde via `invitation_create_request` (§1.3).

### 2.1 Geração e transporte do token (invariante de segurança)

- Token bruto = `base64url(HMAC-SHA256(INVITE_TOKEN_KEY, idempotency_key))` (256 bits),
  **derivado** no BFF a partir de um secret dedicado e da `idempotency_key`. É
  determinístico e reproduzível por qualquer instância (não depende de estado em
  memória), o que torna o retry seguro mesmo após restart/perda de resposta (§1.3).
- Persistência: `sha256(token)` em hex minúsculo. Na **criação**, o BFF envia à API
  apenas o hash; na **consulta/consumo**, o token bruto (que está no browser do
  convidado) é enviado no corpo e a API calcula o hash para comparar.
- Transporte ao convidado: fragmento (`#t=`), nunca path/query. Racional: fragmento
  não é enviado ao servidor em requisições nem vaza em `Referer`/access log.
- Logs registram apenas `request_id` e `invitation_id` resolvido — nunca o token
  nem o hash.

## 3. Autorização — quem convida o quê

Base: `docs/architecture/AUTHORIZATION_MATRIX.md`, seção "Memberships e
compradores", linha **"Convidar/remover seller admin/editor/support"** (`:72`):
`PSA=N, PS=N, PA=N, SO=A, SA=C, SE=N, SS=N, B=N, V=N`, condição
"SA não concede owner nem poder igual/superior proibido". E "Plataforma e tenants",
linha "Conceder/revogar papel global" (`:60`): `PSA=C` com step-up + dupla
aprovação.

> Nota factual: a linha `:72` cobre **apenas** admin/editor/support. **Buyer não
> está nela.** Buyer só aparece em linhas distintas — "Importar
> buyers/entitlements" (`:77`), "Conceder entitlement manual" (`:78`) — com atores
> e condições diferentes (ali `SS=C`). Portanto convite de buyer **não é derivável**
> de `:72`; fica para a Fase 5 (§8), não no MVP.

| Emissor | Pode convidar (MVP) | Não pode convidar | Condição |
|---|---|---|---|
| `seller_owner` (SO) | `seller_admin`, `seller_editor`, `seller_support` | `seller_owner`, papéis de plataforma | membership ativa no tenant do convite (matriz `SO=A`) |
| `seller_admin` (SA) | `seller_editor`, `seller_support` | `seller_admin` (poder igual), `seller_owner` (superior), plataforma | membership ativa; "poder igual/superior proibido" (matriz `SA=C`) |
| `seller_editor` (SE) | — | tudo | `N` |
| `seller_support` (SS) | — | tudo | `N` |
| `platform_superadmin` (PSA) | `platform_support`, `platform_auditor` | `platform_superadmin`, papéis de seller | step-up + dupla aprovação (§4.3, gated) |
| `platform_support` (PS) / `platform_auditor` (PA) | — | tudo | `N` |
| `buyer` (B) / visitante (V) | — | tudo | `N` |

Enforcement: a validação de "quem pode o quê" acontece **dentro da função emissora**
(`SECURITY DEFINER`), consultando `seller_membership`/`platform_grant` do
`context_uuid('identity_user_id')`, jamais confiando em papel vindo do browser.
Falha fechada (`28000`/`403`) quando a condição não puder ser provada (regra 7 da
matriz).

Regra anti-escalação (seller): a função rejeita `seller_owner` sempre e, quando o
emissor é `seller_admin`, rejeita também `seller_admin` (poder igual) — SA só
concede `seller_editor`/`seller_support`.

## 4. Rotas propostas e contratos

Prefixo `/v1` (mesmo `APIRouter` de `routes.py:15`). Toda rota é interna BFF→API,
autenticada por credencial de serviço + envelope assinado
(`internal_requests.py`). Capabilities novas entre colchetes.

### 4.1 Emissor — seller

**`POST /v1/sellers/{sellerId}/invitations`** — cria convite de membership.
Capability `[seller.invitation.create]`. Envelope: `seller_organization_id = sellerId`.

Request (o `token_sha256` é calculado pelo BFF a partir do token gerado uma vez —
§2.1; a API nunca recebe o token bruto na criação):
```json
{
  "idempotency_key": "1f0a7b2c-....-uuid",
  "target_role": "seller_editor",
  "invited_email_hint": "pessoa@exemplo.com",
  "ttl_seconds": 604800,
  "token_sha256": "<64-hex>"
}
```
Validações (Pydantic, molde `SellerOnboardingBody`, `routes.py:38`): `idempotency_key`
UUID canônico; `target_role` no enum permitido para o scope seller
(`seller_admin|seller_editor|seller_support`); `invited_email_hint` opcional
normalizado; `ttl_seconds` inteiro em faixa `[3600, 604800]` (1h–7d, teto
  fixado pela `EMAIL_POLICY.md` §4 em 02/09; era 14d);
`token_sha256` casando `^[0-9a-f]{64}$`.

Response `201`:
```json
{
  "id": "uuid-do-convite",
  "invite_url": "https://sell.autonomia.site/invite#t=<token-bruto-uma-vez>",
  "target_role": "seller_editor",
  "expires_at": "2026-07-24T12:00:00Z"
}
```
O `invite_url` (com token) é montado pelo **BFF**, que **recomputa o token
deterministicamente** da `idempotency_key` (§2.1) — inclusive em replays. Nunca é
persistido em claro (só o hash fica em repouso).

Erros: `403` sem membership/role suficiente ou tentativa de escalar papel; `409`
`invitation_conflict` (replay de `idempotency_key` com corpo divergente, ou dedup
de convite pendente); `422` corpo inválido.

**`GET /v1/sellers/{sellerId}/invitations`** — lista convites do tenant.
Capability `[seller.invitation.list]`. Envelope: `seller_organization_id = sellerId`.
Response `200`: lista de `{id, scope, target_role, invited_email_hint, status,
expires_at, created_at, consumed_at}`. **Nunca** retorna `token_sha256` nem token.

**`POST /v1/sellers/{sellerId}/invitations/{invitationId}/revoke`** — revoga pendente.
Capability `[seller.invitation.revoke]`. Envelope: `seller_organization_id = sellerId`.
Via `POST` (transição de estado auditável, consistente com o padrão de mutation do
repo — cf. `POST /v1/me/sellers`, `routes.py:186`), não `DELETE`.
CAS: `update invitation set status='revoked', revoked_at=...,
revoked_by_identity_user_id=... where id=:id and seller_organization_id=:tenant
and status='pending'`. `200` `{"status": "revoked"}`; `409` se já
consumido/revogado; `404` se não pertence ao tenant.

### 4.2 Convidado — preview e consumo

**`POST /v1/invitations/preview`** — projeção mínima do convite (pós-login).
Capability `[invitation.preview]`. Envelope: `seller_organization_id = NULL`. Token
no corpo, nunca em URL.

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
O `410` vem do SQLSTATE de aplicação `P0410` mapeado no router de convites.

**`POST /v1/invitations/consume`** — consome o convite e aplica o efeito.
Capability `[invitation.consume]`. Envelope: `seller_organization_id = NULL`
(pré-grant, como `onboard_seller`). A identidade já foi sincronizada por
`PUT /v1/me`.

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
Erros:
- `401` se o envelope trouxer `seller_organization_id != NULL` — o guard
  `_require_no_seller` (`routes.py:230-233`) responde **401 UNAUTHORIZED** (não
  403); pré-grant exige contexto sem tenant.
- `403` se a capability/contexto não conferir (SQLSTATE `28000`/`42501`
  → `routes.py:266-267`).
- `410 invitation_expired`/`invitation_revoked` (SQLSTATE `P0410`).
- `409 invitation_already_consumed` (consumido por outra identidade) ou
  `invitation_idempotency_conflict` (mesma `idempotency_key`, corpo diferente).
- `422` corpo inválido.

### 4.3 Emissor — plataforma (sequenciado por último, gated)

**`POST /v1/platform/invitations`** — convida `platform_support`/`platform_auditor`.
Capability `[platform.invitation.create]`. Envelope: `seller_organization_id = NULL`.
Requer que o contexto tenha `platform_superadmin` ativo **e** um registro de
segunda aprovação + assurance step-up (mfa/passkey). Enquanto o fluxo de dupla
aprovação e o assurance real não existirem (D-005 `Pending spike/Blocked`), esta
rota permanece atrás de flag desabilitada (a tabela e a função já suportam).
Contrato análogo ao 4.1, sem `seller_organization_id`.

## 5. Invariantes de segurança

1. **Token nunca em log/URL/telemetria.** Só trafega no corpo BFF↔API (bruto apenas
   no consumo; hash na criação) e no fragmento do link. Logs registram `request_id`
   + `invitation_id` resolvido. Sanitização defensiva: qualquer string
   `[A-Za-z0-9_\-]{32,}` em mensagem de erro vira `<REDACTED>`.
2. **Hash em repouso.** `token_sha256` é a única forma persistida; coluna com
   `check` de formato hex-64. Impossível recuperar o token a partir do banco.
3. **Uso único por CAS.** O consumo é `update ... where status='pending' and
   expires_at > clock_timestamp()` com `pg_advisory_xact_lock` no hash; duas
   requisições concorrentes resultam em exatamente um `consumed` e um `409`/`410`.
4. **Expiração.** `expires_at` obrigatório; consumo/preview tratam `expires_at <= now`
   como `410`. Sweeper opcional marca `pending` vencidos, mas a decisão de
   autorização nunca depende do sweeper — sempre revalida `expires_at` na transação.
5. **Revogação.** CAS `pending → revoked` via `POST .../revoke`; convite revogado
   nunca consome.
6. **Sem escalada de privilégio.** `target_role` limitado por scope no schema;
   emissor `seller_admin` não concede `admin`/`owner`; `platform_superadmin` nunca
   é alvo de convite. Falha fechada.
7. **Tenant vem da linha, não do browser.** `consume` exige `envelope.seller = NULL`
   e deriva o tenant de `invitation.seller_organization_id`. O CHECK
   `invitation_result_tenant_ck` reforça que o resultado não cai em outro tenant.
8. **Pré-grant isolado.** O consumo não concede privilégio global implícito; cria só
   a membership descrita na linha, dentro de transação própria (linha pré-grant
   proposta à matriz — §0).
9. **RLS ligada na tabela nova.** `invitation` tem `enable/force row level security`
   (como todas as tabelas de domínio) e policies por capability/tenant (§1.1);
   escritas só via função `SECURITY DEFINER`; `appsell_runtime` só `select`. Sem
   isso, as policies não protegem nada.
10. **Enforcement em camadas** (matriz "Enforcement"): BFF (sessão+CSRF+origin) →
    API (capability+objeto) → função `SECURITY DEFINER` (papel do emissor) →
    RLS/constraints (tenant/uso único). Teste negativo obrigatório em cada camada.
11. **Gate D-029 — decidida, canal pendente.** D-029 foi decidida em #355
    (`docs/architecture/EMAIL_POLICY.md`): o convite é e-mail **transacional**,
    sai por `sell@autonomia.site` e não tem descadastro. O MVP entrega
    **emissão de link + consumo**; o envio real espera o canal transacional da
    #356. PR-5 depende desse canal (§7).

## 6. Critérios de aceite testáveis (hard-assert, por rota)

Cada critério é um teste de integração com Postgres real (padrão do repo: migrations
em banco vazio + fluxo). Zero soft pass; timeout = FAIL.

### `POST /v1/sellers/{sellerId}/invitations`
- AC-C1: SO cria convite `seller_editor` → `201`, `invite_url` presente, linha
  `invitation` com `status='pending'` e `token_sha256` de 64 hex; **assert** que o
  token bruto não aparece em nenhuma coluna nem em `audit_event`, e que a API
  recebeu apenas o hash (não o token bruto) no corpo.
- AC-C2: SA cria `seller_support` → `201`. SA tenta criar `seller_admin` → `403`
  (poder igual). SA tenta `seller_owner` → `403`.
- AC-C3: SE/SS/buyer/visitante chamando a rota → `403`.
- AC-C4: replay do mesmo `idempotency_key` **com mesmo corpo (mesmo `token_sha256`)**
  → mesmo `invitation.id`, mesmo `token_sha256` persistido, sem segundo insert;
  **assert** que o `invite_url` remontável pelo BFF ainda é válido. Com corpo
  diferente (`token_sha256` divergente) → `409`.
- AC-C5: segundo convite `pending` para mesmo (tenant, papel, e-mail) com **nova**
  `idempotency_key` → `409` dedup.
- AC-C6: `ttl_seconds` fora de `[3600,1209600]` → `422`; `token_sha256` fora do
  formato hex-64 → `422`.
- AC-C7: emissor de outro tenant (envelope.seller = outro) → `403`/RLS nega.
- AC-C8: **retry após "commit + resposta perdida" em nova instância** — a API commita
  a criação e a resposta é descartada; uma **nova instância do BFF** (sem estado em
  memória) refaz a chamada com a **mesma `idempotency_key`** → recomputa o mesmo
  token (KDF, §2.1), recebe o **mesmo `invitation_id`**, remonta o **mesmo
  `invite_url`**; **assert** que o `token_sha256` no banco é idêntico ao da primeira
  chamada e que **não** houve segundo insert.

### `GET /v1/sellers/{sellerId}/invitations`
- AC-L1: SO/SA listam só convites do próprio tenant; **assert** ausência de
  `token_sha256`/token no payload.
- AC-L2: identidade sem membership no tenant → `403`.

### `POST /v1/sellers/{sellerId}/invitations/{id}/revoke`
- AC-R1: SO revoga `pending` → `200`, `status='revoked'`,
  `revoked_by_identity_user_id` = emissor.
- AC-R2: revogar convite já `consumed` → `409`; revogar de outro tenant → `404`.
- AC-R3: após revogar, `consume` do mesmo token → `410 invitation_revoked`.
- AC-R4: **corrida revoke × consume** — `revoke` e `consume` concorrentes sobre o
  mesmo convite `pending` → exatamente um vence; resultado consistente
  (ou `consumed`+`409` no revoke, ou `revoked`+`410` no consume); nunca ambos.

### `POST /v1/invitations/preview`
- AC-P1: token válido → projeção mínima correta; **assert** ausência de PII do
  emissor e de qualquer hash/token.
- AC-P2: token inexistente → `404`; expirado → `410`; revogado → `410`.
- AC-P3: token em querystring (não no corpo) → rota não aceita (o contrato só lê corpo).

### `POST /v1/invitations/consume`
- AC-X1: convidado novo consome `seller_editor` → cria `identity_user` (via
  `PUT /v1/me` anterior) + `seller_membership(role=seller_editor, status=active)` no
  tenant da linha; `invitation.status='consumed'`, `consumed_by_identity_user_id`
  correto, `audit_event` gravado.
- AC-X2: **uso único, chaves distintas** — duas chamadas concorrentes com o mesmo
  token mas **`idempotency_key` e/ou identidade distintas** → exatamente um `200` e
  um `409 invitation_already_consumed`; exatamente uma membership criada.
- AC-X3: **replay idempotente** — mesma `idempotency_key`, mesmo corpo, mesma
  identidade → `200` + `200` (mesmo resultado), sem segunda membership.
- AC-X4: token expirado → `410`; revogado → `410`.
- AC-X5: envelope com `seller_organization_id != NULL` → **`401`** (guard
  `_require_no_seller`, `routes.py:230-233`, responde 401 UNAUTHORIZED; **não 403**).
- AC-X6: convidado que já é membro ativo do tenant no papel alvo → **tratamento
  idempotente**: retorna o efeito existente e marca o convite `consumed` (não `409`),
  registrando em `audit_event`. (Decisão do Rodrigo: já-membro = idempotente.)
- AC-X7: consumo cross-tenant impossível — o tenant do efeito é sempre
  `invitation.seller_organization_id`; tentativa de forçar outro tenant falha por
  função + CHECK `invitation_result_tenant_ck`.

### `POST /v1/platform/invitations` (gated)
- AC-PL1: com flag desabilitada → `404`/`403` determinístico.
- AC-PL2: (quando habilitado) sem dupla aprovação/step-up → `403`; PSA com ambos →
  cria convite `platform_auditor`; alvo `platform_superadmin` → `403`.

## 7. Plano de PRs pequenas

Cada PR: DRAFT, `Refs #95` (`Part of #92`), migrations forward-only, testes
negativos por camada, sem merge sem OK do Rodrigo. Reviewer + tester obrigatórios
antes de "ready" (regras 4/9 globais).

1. **PR-1 · Migration `0018_membership_invitations`** (`down_revision =
   "0017_pdf_preraster"`) — tabela `invitation`,
   `app_private.invitation_create_request`, `app_private.invitation_consume_request`,
   funções `create_seller_invitation`, `consume_invitation`,
   `revoke_seller_invitation`, RLS (`enable/force` + policies por capability),
   grants a `appsell_runtime` (só `select` + execute), `schema_ready()` estendida.
   Testes: migração em banco vazio e vindo de `0017`; constraints, RLS e CAS via
   SQL direto. **Gate:** aprovação do Rodrigo à nova linha pré-grant da matriz (§0).
2. **PR-2 · Rotas emissor seller** — `POST/GET /v1/sellers/{sellerId}/invitations`,
   `POST .../revoke`, Pydantic bodies (incl. `token_sha256`), mapeamento de erro
   (molde `routes.py:257`) + `P0410→410`. Testes: AC-C*, AC-L*, AC-R*.
3. **PR-3 · Resolução no callback** — `POST /v1/invitations/preview` e
   `POST /v1/invitations/consume`, integração com `PUT /v1/me` no callback BFF.
   Testes: AC-P*, AC-X*.
4. **PR-4 · Emissor plataforma (gated)** — `POST /v1/platform/invitations` atrás de
   flag, dupla aprovação + step-up. Testes: AC-PL*. Depende do fluxo de grants
   globais e de D-005 (assurance/MFA, `Pending spike/Blocked`).
5. **PR-5 · UI + envio** — tela de emissão (produtor), listagem/revogação e página
   `/invite` (lê fragmento, dispara login, consome no callback). D-029 está
   **decidida** (#355, `docs/architecture/EMAIL_POLICY.md`: convite é
   transacional, base legal legítimo interesse, um envio por convite); o envio
   real **depende do canal transacional da #356** e, até ele existir, os testes
   E2E mobile das jornadas usam destinatários sintéticos.

Sequência obrigatória: PR-1 → PR-2 → PR-3 → (PR-4 gated) → PR-5, cada uma atrás do
gate da anterior (regra 7 do §7 do TECHNICAL_PLAN).

## 8. Dúvidas abertas para decisão do Rodrigo

1. **Adicionar "Consumir convite de membership" como fluxo pré-grant na
   `AUTHORIZATION_MATRIX.md`.** Hoje a seção "Fluxos pré-grant" (`:41-50`) lista só
   `Bootstrap inicial` e `Criar tenant próprio`. Esta spec depende de uma terceira
   linha explícita. Como a matriz é "Approved baseline with gates", isso é
   **mudança de baseline** e precisa do teu OK antes de PR-1. Recomendação: aprovar
   a linha com condição "token de uso único válido, não expirado, não revogado;
   cria só a membership da linha; sem privilégio global".
2. **Convite `buyer` agora ou só na Fase 5?** Correção do achado #2 do review: o
   modelo de entitlement **não** está "por migrar" — `buyer_entitlement` **existe
   desde `0006_demo_learning.py:20`**, e `0007_learning_progress.py:184-188` já
   torna `publication_id` `NOT NULL` com FK composta
   `(seller_organization_id, catalog_product_id, publication_id) → publication`.
   Portanto o convite de buyer é adiado **não** por falta de schema, mas porque:
   (a) emitir entitlement exige um `publication` alvo (FK composta + `reference_key`
   + `source`), fora do escopo "promover a papel de staff" deste bloco; e (b)
   depende de D-027 ("Política de entitlement manual/importação", `Pending
   business`). Recomendação: manter buyer **fora** do MVP; na Fase 5, adicionar
   `scope='buyer'` + `target_publication_id` com a FK composta acima e materialização
   coordenada com D-027.
3. **AC-X6 (convidado já é membro):** confirmado idempotente-retorna-efeito (marca
   `consumed`, não `409`), conforme tua régua ("convidado já-membro = idempotente").
   Registrado em §6.
4. **Rota de revoke:** confirmado `POST .../revoke` (transição de estado auditável),
   não `DELETE`, por consistência com o padrão de mutation do repo. Registrado em §4.1.
