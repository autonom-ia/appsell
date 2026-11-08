"""Acesso em compra com disputa de pagamento (#542).

O Financial passa a avisar o Sell da disputa de pagamento de uma venda pelo
mesmo caminho do estorno: um evento assinado entra pelo ingresso de webhook,
vira `fulfillment_job` e é aplicado pelo worker, com lease, fence e retomada.
São três eventos novos:

* `purchase.dispute_opened` — disputa aberta;
* `purchase.dispute_won` — a loja ganhou;
* `purchase.dispute_lost` — a loja perdeu.

Cada evento de disputa traz o id da disputa na Stripe e a data em que ela foi
aberta lá. A api grava os dois, com o id só em hash, em
`app_private.checkout_dispute_event`, na mesma transação do evento, pela
função `accept_checkout_dispute_webhook` (a aceitação de sempre mais essa
gravação).

O que muda no banco:

* as checagens de tipo de evento de `webhook_event`, `fulfillment_job` e das
  funções de ingresso passam a aceitar os três tipos;
* `entitlement_grant.status` ganha `suspended` (disputa aberta) e
  `charged_back` (disputa perdida, distinto de `refunded`, que segue sendo só
  estorno);
* `buyer_entitlement.status` ganha `suspended`. Quem filtra `status='active'`
  já nega o acesso sem mudança;
* `app_private.purchase_dispute` guarda o estado de cada disputa da compra
  (uma compra pode ter mais de uma);
* `app_private.dispute_access_switch` liga a regra loja por loja. É um
  histórico só de inserção (quem, quando, por quê): a última linha da loja
  decide, e `disable` desliga sem apagar quem ligou. Vazia, a regra não vale
  para ninguém: o evento é aceito, o job é concluído e nada muda no acesso.
  Fica no banco, e não em env, porque a regra é decidida dentro do SQL, sob o
  lock da compra; só `apply_checkout_dispute` lê a tabela;
* `app_private.apply_checkout_dispute` aplica a regra. A função de
  fulfillment (`apply_checkout_fulfillment`) chama essa função para os três
  tipos novos, depois de já ter validado o lease e tomado os dois locks da
  compra e do par (comprador, produto);
* a conciliação (#147) passa a ver a disputa: compra suspensa ou perdida com
  acesso ativo, acesso suspenso sem disputa, compra suspensa em loja desligada,
  evento de disputa sem job e evento de disputa sem data.

A regra, por disputa:

* o desfecho vence a abertura, em qualquer ordem de chegada; a perdida vence
  a ganha. Não se usa o `occurred_at` para ordenar: num retry do Financial
  ele muda;
* vale só a disputa aberta na Stripe depois de a loja ser ligada (a data real
  da disputa contra a linha `enable` vigente). A anterior fica registrada sem
  efeito, e isso é fixado na primeira vez que ela chega;
* loja desligada: disputa nova é ignorada, mas o desfecho de uma disputa que
  já vale continua sendo aplicado (quem ganhou não fica suspenso para sempre);
* a data só vale vinda do evento assinado. Se faltar ou vier inválida (sem
  UTC, ou depois do evento), a disputa é gravada sem data e sem efeito no
  acesso, e a conciliação alerta (`dispute_without_created_at`). Nunca cai
  para "agora".

E por compra, a partir das disputas que valem:

* alguma perdida: o grant vira `charged_back`, que é final;
* senão, alguma aberta: o grant vira `suspended`;
* senão: `active`;
* estorno vence: `refunded` não muda por disputa;
* `purchase.approved` repetido não reativa um grant suspenso nem perdido.

O acesso do par (comprador, produto) é recalculado depois de cada mudança:
`active` se algum grant está ativo; senão `suspended` se algum está suspenso;
senão `revoked`. Só muda um acesso que estava `active` ou `suspended`; um acesso
expirado ou revogado por outro motivo não volta por disputa. Se a disputa foi
ganha e o acesso nunca chegou a existir (a disputa chegou antes da aprovação),
ele é criado, como na aprovação, desde que a aprovação já tenha sido aplicada.

O estorno passa a usar a mesma regra: estornada a compra, o acesso fica
`suspended` se outra compra do par está suspensa, e não `revoked`. E o estorno
não troca `charged_back` por `refunded`.

A função de fulfillment e a da conciliação são reescritas pelo padrão da 0049
(`pg_get_functiondef` com `replace` ancorado, cada âncora exatamente uma vez).
As assinaturas não mudam, então a api em produção segue funcionando entre a
migration e o deploy.

As três tabelas novas têm RLS `enable` + `force`, só com a policy do dono, e o
`schema_ready` confere as três e as duas funções novas.

A descida recusa enquanto existir evento de disputa, grant suspenso ou
perdido, acesso suspenso ou linha na chave por loja: esse estado não tem como
voltar ao esquema anterior sem decidir o acesso de alguém ou apagar auditoria.
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0074_acesso_em_disputa"
down_revision = "0072_relatorio_de_vendas"
branch_labels = None
depends_on = None

_APPLY = "app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text)"
_APPLY_DISPUTA = (
    "app_private.apply_checkout_dispute("
    "uuid,uuid,uuid,uuid,text,uuid,text,timestamptz,uuid,uuid,uuid)"
)
_ACEITA_DISPUTA = (
    "app_private.accept_checkout_dispute_webhook("
    "uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,"
    "smallint,timestamptz,bigint,uuid,text,text,text,timestamptz)"
)
_RELATORIO = "app_private.checkout_reconciliation_report(timestamptz,timestamptz,interval)"
_TABELAS_NOVAS: tuple[str, ...] = (
    "app_private.dispute_access_switch",
    "app_private.checkout_dispute_event",
    "app_private.purchase_dispute",
)

_TIPOS_DE_ANTES = "'purchase.approved','purchase.refunded','subscription.cancelled'"
_TIPOS_DE_DISPUTA = "'purchase.dispute_opened','purchase.dispute_won','purchase.dispute_lost'"
_TIPOS_DE_AGORA = _TIPOS_DE_ANTES + "," + _TIPOS_DE_DISPUTA


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}_para_0074", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A lista do readiness vem da anterior na cadeia (`_READY_ATE_0072`), não de
# uma cópia escrita aqui: assim ela não perde as versões que entraram antes.
READY_ANTES: str = _carregar(down_revision)._READY_ATE_0072
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0074 = READY_ANTES[:-2] + f",'{revision}'))"
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
# Âncora do trecho de funções do readiness: o texto que a 0064 deixou. A 0068
# também acrescenta logo depois dessa âncora; ela continua aparecendo uma vez
# só, e a troca põe as linhas da disputa entre a âncora e as da 0068.
_ULTIMA_DO_READY = "0064_vendas_ativadas_pela_loja"

_READY_DA_DISPUTA = (
    "            and to_regclass('app_private.dispute_access_switch') is not null\n"
    "            and to_regclass('app_private.checkout_dispute_event') is not null\n"
    "            and to_regclass('app_private.purchase_dispute') is not null\n"
    "            and (select count(*) from pg_catalog.pg_class tabela\n"
    "              where tabela.oid in (to_regclass('app_private.dispute_access_switch'),\n"
    "                to_regclass('app_private.checkout_dispute_event'),\n"
    "                to_regclass('app_private.purchase_dispute'))\n"
    "                and tabela.relrowsecurity and tabela.relforcerowsecurity) = 3\n"
    "            and to_regprocedure('app_private.apply_checkout_dispute("
    "uuid,uuid,uuid,uuid,text,uuid,text,timestamptz,uuid,uuid,uuid)') is not null\n"
    "            and to_regprocedure('app_private.accept_checkout_dispute_webhook("
    "uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,"
    "smallint,timestamptz,bigint,uuid,text,text,text,timestamptz)') is not null\n"
)


def _migration_do_ready() -> ModuleType:
    """Lê a âncora que a 0064 deixou no `schema_ready`, em vez de copiá-la."""
    caminho = Path(__file__).with_name(f"{_ULTIMA_DO_READY}.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0064_para_0074", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError("a migration 0064 nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _ready_funcao() -> tuple[str, str]:
    de = _migration_do_ready()._ready_funcao()[1]
    return de, de + _READY_DA_DISPUTA


# Marcadores com sublinhado: o texto trocado pode conter "DE" ou "PARA".
_REESCREVE = """
do $migration$
declare definicao text; reescrita text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if (length(definicao)-length(replace(definicao,$de$__DE__$de$,'')))
       / length($de$__DE__$de$) <> 1 then
    raise exception '__ALVO__ nao continha a ancora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao,$de$__DE__$de$,$para$__PARA__$para$);
  execute reescrita;
end $migration$
"""


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("__ALVO__", alvo).replace("__DE__", de).replace("__PARA__", para)


# Toda função de ingresso que lista os tipos aceitos. Pelo menos uma tem de
# existir (accept_checkout_webhook); cada uma contém a lista exatamente uma vez
# e é reescrita com a lista nova.
_TROCA_TIPOS_NAS_FUNCOES = """
do $migration$
declare alvo record; achadas integer := 0; definicao text;
begin
  for alvo in
    select proc.oid
    from pg_proc proc
    join pg_namespace ns on ns.oid = proc.pronamespace
    where ns.nspname in ('public','app_private')
      and position($de$__DE__$de$ in proc.prosrc) > 0
  loop
    definicao := pg_get_functiondef(alvo.oid);
    if (length(definicao)-length(replace(definicao,$de$__DE__$de$,'')))
         / length($de$__DE__$de$) <> 1 then
      raise exception '% nao continha a lista de tipos exatamente uma vez',
        alvo.oid::regprocedure using errcode='55000';
    end if;
    execute replace(definicao,$de$__DE__$de$,$para$__PARA__$para$);
    achadas := achadas + 1;
  end loop;
  if achadas < 1 then
    raise exception 'nenhuma funcao de ingresso com a lista de tipos' using errcode='55000';
  end if;
end $migration$
"""

# Cada tabela tem exatamente uma checagem com a lista de tipos. `not valid` e
# depois `validate`: a troca não segura a tabela durante a varredura.
_TROCA_TIPOS_NAS_TABELAS = """
do $migration$
declare alvo record; achadas integer := 0;
begin
  for alvo in
    select con.conrelid::regclass::text as tabela, con.conname
    from pg_constraint con
    where con.contype = 'c'
      and con.conrelid in ('public.webhook_event'::regclass,
                           'app_private.fulfillment_job'::regclass)
      and position('subscription.cancelled' in pg_get_constraintdef(con.oid)) > 0
  loop
    execute format('alter table %s drop constraint %I', alvo.tabela, alvo.conname);
    execute format('alter table %s add constraint %I check (event_type in (%s)) not valid',
      alvo.tabela, alvo.conname, $tipos$__TIPOS__$tipos$);
    execute format('alter table %s validate constraint %I', alvo.tabela, alvo.conname);
    achadas := achadas + 1;
  end loop;
  if achadas <> 2 then
    raise exception 'esperava 2 checagens de tipo de evento, achei %', achadas
      using errcode='55000';
  end if;
end $migration$
"""

# A checagem de status de uma tabela, achada pelo texto que só ela tem.
_TROCA_STATUS = """
do $migration$
declare alvo record; achadas integer := 0;
begin
  for alvo in
    select con.conname
    from pg_constraint con
    where con.contype = 'c'
      and con.conrelid = '__TABELA__'::regclass
      and position($marca$__MARCA__$marca$ in pg_get_constraintdef(con.oid)) > 0
  loop
    execute format('alter table __TABELA__ drop constraint %I', alvo.conname);
    execute format('alter table __TABELA__ add constraint %I check (status in (%s)) not valid',
      alvo.conname, $estados$__ESTADOS__$estados$);
    execute format('alter table __TABELA__ validate constraint %I', alvo.conname);
    achadas := achadas + 1;
  end loop;
  if achadas <> 1 then
    raise exception 'esperava 1 checagem de status em __TABELA__, achei %', achadas
      using errcode='55000';
  end if;
end $migration$
"""


def _troca_status(tabela: str, marca: str, estados: str) -> str:
    return (
        _TROCA_STATUS.replace("__TABELA__", tabela)
        .replace("__MARCA__", marca)
        .replace("__ESTADOS__", estados)
    )


_GRANT = "app_private.entitlement_grant"
_ENTITLEMENT = "public.buyer_entitlement"
_GRANT_ANTES = "'active','refunded'"
_GRANT_AGORA = "'active','refunded','suspended','charged_back'"
_ENTITLEMENT_ANTES = "'active','revoked','expired'"
_ENTITLEMENT_AGORA = "'active','revoked','expired','suspended'"

# Hash hexadecimal minúsculo de 64 caracteres, sem expressão regular.
_HASH_OK = "length(%s)=64 and ltrim(%s,'0123456789abcdef')=''"

_CHAVE_POR_LOJA = """
create table app_private.dispute_access_switch (
  id bigint generated always as identity primary key,
  seller_organization_id uuid not null references public.seller_organization(id),
  action text not null check (action in ('enable','disable')),
  actor text not null check (length(btrim(actor)) between 1 and 200),
  reason text not null check (length(btrim(reason)) between 1 and 1000),
  created_at timestamptz not null default clock_timestamp()
)
"""

_INDICE_DA_CHAVE = """
create index dispute_access_switch_seller_idx
  on app_private.dispute_access_switch(seller_organization_id,created_at desc,id desc)
"""

# Histórico só de inserção: nem o dono reescreve quem ligou ou desligou.
_SO_INSERCAO = """
create function app_private.dispute_access_switch_append_only() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  raise exception 'dispute_access_switch is append-only' using errcode='55000';
end $$
"""

_GATILHO_SO_INSERCAO = """
create trigger dispute_access_switch_append_only
  before update or delete on app_private.dispute_access_switch
  for each row execute function app_private.dispute_access_switch_append_only()
"""

# O que o evento de disputa diz da disputa, gravado na aceitação do evento.
_DADOS_DO_EVENTO = f"""
create table app_private.checkout_dispute_event (
  seller_organization_id uuid not null,
  webhook_event_id uuid not null,
  dispute_id_sha256 text not null check ({_HASH_OK % ("dispute_id_sha256", "dispute_id_sha256")}),
  -- Nula quando o evento veio sem data válida: a disputa não muda acesso.
  dispute_created_at timestamptz,
  recorded_at timestamptz not null default clock_timestamp(),
  constraint checkout_dispute_event_pkey primary key (seller_organization_id,webhook_event_id),
  constraint checkout_dispute_event_event_fkey foreign key (seller_organization_id,webhook_event_id)
    references public.webhook_event(seller_organization_id,id)
)
"""

# O estado de cada disputa da compra, mantido pela função de disputa.
_DISPUTA_DA_COMPRA = f"""
create table app_private.purchase_dispute (
  seller_organization_id uuid not null,
  checkout_integration_id uuid not null,
  external_purchase_id_sha256 text not null check (
    {_HASH_OK % ("external_purchase_id_sha256", "external_purchase_id_sha256")}),
  dispute_id_sha256 text not null check ({_HASH_OK % ("dispute_id_sha256", "dispute_id_sha256")}),
  state text not null check (state in ('opened','won','lost')),
  dispute_created_at timestamptz,
  effective boolean not null,
  check (dispute_created_at is not null or not effective),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  constraint purchase_dispute_pkey primary key (seller_organization_id,checkout_integration_id,
    external_purchase_id_sha256,dispute_id_sha256),
  constraint purchase_dispute_integration_fkey
    foreign key (seller_organization_id,checkout_integration_id)
    references public.checkout_integration(seller_organization_id,id)
)
"""


def _rls(tabela: str) -> tuple[str, ...]:
    nome = tabela.removeprefix("app_private.")
    return (
        f"alter table {tabela} enable row level security",
        f"alter table {tabela} force row level security",
        f"create policy {nome}_owner_all on {tabela} to appsell_owner using (true) with check (true)",
        f"revoke all on table {tabela} from public,appsell_runtime",
    )


_ACEITA = """
create function app_private.accept_checkout_dispute_webhook(
  p_seller uuid,p_endpoint uuid,p_endpoint_key text,p_secret_reference text,
  p_endpoint_revision bigint,p_event_sha text,p_event_octets smallint,p_payload_sha text,
  p_payload_octets integer,p_event_type text,p_product_sha text,p_product_octets smallint,
  p_purchase_sha text,p_purchase_octets smallint,p_occurred timestamptz,p_signed_at bigint,
  p_request uuid,p_buyer_email text,p_buyer_sub text,
  p_dispute_sha text,p_dispute_created_at timestamptz
) returns table(webhook_event_id uuid,outbox_event_id uuid,created boolean)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
#variable_conflict use_column
declare aceito record; gravado record;
begin
  if p_event_type is null or p_event_type not in (__DISPUTA__)
     or p_dispute_sha is null or length(p_dispute_sha)<>64
     or ltrim(p_dispute_sha,'0123456789abcdef')<>''
     or p_occurred is null then
    raise exception 'dispute webhook is invalid' using errcode='22023';
  end if;
  -- Data da disputa ausente ou depois do evento: grava sem data, nunca
  -- "agora". A disputa fica sem efeito e a conciliação alerta.
  if p_dispute_created_at > p_occurred then
    p_dispute_created_at := null;
  end if;
  -- A aceitação de sempre: contexto, assinatura do endpoint, dedupe e job.
  select * into strict aceito from app_private.accept_checkout_webhook(
    p_seller,p_endpoint,p_endpoint_key,p_secret_reference,p_endpoint_revision,
    p_event_sha,p_event_octets,p_payload_sha,p_payload_octets,p_event_type,
    p_product_sha,p_product_octets,p_purchase_sha,p_purchase_octets,p_occurred,
    p_signed_at,p_request,p_buyer_email,p_buyer_sub);
  insert into app_private.checkout_dispute_event(
    seller_organization_id,webhook_event_id,dispute_id_sha256,dispute_created_at
  ) values (p_seller,aceito.webhook_event_id,p_dispute_sha,p_dispute_created_at)
  on conflict on constraint checkout_dispute_event_pkey do nothing;
  -- Reentrega do mesmo evento: os dados da disputa têm de ser os mesmos.
  select evento.dispute_id_sha256,evento.dispute_created_at into strict gravado
  from app_private.checkout_dispute_event evento
  where evento.seller_organization_id=p_seller
    and evento.webhook_event_id=aceito.webhook_event_id;
  if gravado.dispute_id_sha256<>p_dispute_sha
     or gravado.dispute_created_at is distinct from p_dispute_created_at then
    raise exception 'dispute details conflict' using errcode='23505';
  end if;
  return query select aceito.webhook_event_id,aceito.outbox_event_id,aceito.created;
end $$
"""

_FUNCAO_DISPUTA = """
create function app_private.apply_checkout_dispute(
  p_worker uuid,p_seller uuid,p_job uuid,p_request uuid,p_event_type text,
  p_integration uuid,p_purchase text,p_occurred timestamptz,p_webhook_event uuid,
  p_buyer uuid,p_product uuid
) returns jsonb language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  detalhe record; chave record; estado text; alvo text; estado_gravado text;
  vale boolean; ligada boolean := false; piso timestamptz; ja_vale boolean;
  grant_status text; resultado text; acesso text; v_entitlement_id uuid;
  v_publication uuid; mudou boolean := false;
begin
  -- Chamada só de dentro de apply_checkout_fulfillment, que já validou o
  -- worker, o lease e tomou os locks da compra e do par (comprador, produto).
  if app_private.context_text('capability')
       is distinct from 'commerce.fulfillment.worker'
     or app_private.context_uuid('identity_user_id') is distinct from p_worker
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller then
    raise exception 'dispute apply is not authorized' using errcode='28000';
  end if;
  estado := case p_event_type
    when 'purchase.dispute_opened' then 'opened'
    when 'purchase.dispute_won' then 'won'
    when 'purchase.dispute_lost' then 'lost'
  end;
  if estado is null then
    raise exception 'unknown dispute event' using errcode='22023';
  end if;
  select evento.dispute_id_sha256,evento.dispute_created_at into detalhe
  from app_private.checkout_dispute_event evento
  where evento.seller_organization_id=p_seller and evento.webhook_event_id=p_webhook_event;
  if not found then
    -- Evento de disputa aceito sem os dados da disputa: o job volta à fila
    -- e acaba em dead_letter, visível na conciliação.
    raise exception 'dispute details are missing' using errcode='55000';
  end if;

  select ultima.action,ultima.created_at into chave
  from app_private.dispute_access_switch ultima
  where ultima.seller_organization_id=p_seller
  order by ultima.created_at desc, ultima.id desc limit 1;
  if found then
    ligada := chave.action = 'enable';
    piso := chave.created_at;
  end if;
  select disputa.effective into ja_vale
  from app_private.purchase_dispute disputa
  where disputa.seller_organization_id=p_seller
    and disputa.checkout_integration_id=p_integration
    and disputa.external_purchase_id_sha256=p_purchase
    and disputa.dispute_id_sha256=detalhe.dispute_id_sha256;
  -- Loja desligada: disputa nova não é registrada. O desfecho de uma disputa
  -- que já vale continua sendo aplicado; sem isso, quem ganhou depois do
  -- desligamento ficaria suspenso para sempre.
  if not ligada and not coalesce(ja_vale,false) then
    resultado := 'dispute_access_disabled';
  else
    -- Estado por disputa: o desfecho vence a abertura em qualquer ordem, e a
    -- perdida vence a ganha. Vale só a disputa aberta depois de a loja ser
    -- ligada; sem data confiável, não vale. Isso é fixado na primeira vez que
    -- a disputa chega.
    insert into app_private.purchase_dispute as atual(
      seller_organization_id,checkout_integration_id,external_purchase_id_sha256,
      dispute_id_sha256,state,dispute_created_at,effective
    ) values (
      p_seller,p_integration,p_purchase,detalhe.dispute_id_sha256,estado,
      detalhe.dispute_created_at,
      ligada and coalesce(detalhe.dispute_created_at >= piso,false)
    )
    on conflict on constraint purchase_dispute_pkey do update set
      state=case
        when atual.state='lost' or excluded.state='lost' then 'lost'
        when atual.state='won' or excluded.state='won' then 'won'
        else 'opened' end,
      dispute_created_at=coalesce(atual.dispute_created_at,excluded.dispute_created_at),
      updated_at=clock_timestamp()
    returning atual.state,atual.effective into estado_gravado,vale;

    alvo := case
      when exists(select 1 from app_private.purchase_dispute disputa
                  where disputa.seller_organization_id=p_seller
                    and disputa.checkout_integration_id=p_integration
                    and disputa.external_purchase_id_sha256=p_purchase
                    and disputa.effective and disputa.state='lost') then 'charged_back'
      when exists(select 1 from app_private.purchase_dispute disputa
                  where disputa.seller_organization_id=p_seller
                    and disputa.checkout_integration_id=p_integration
                    and disputa.external_purchase_id_sha256=p_purchase
                    and disputa.effective and disputa.state='opened') then 'suspended'
      else 'active'
    end;

    select grant_row.status into grant_status
    from app_private.entitlement_grant grant_row
    where grant_row.seller_organization_id=p_seller
      and grant_row.checkout_integration_id=p_integration
      and grant_row.external_purchase_id_sha256=p_purchase
    for update;
    if not found then
      if alvo = 'active' then
        -- Nada a suspender: o grant nasce na aprovação.
        resultado := case when vale then 'grant_pending'
          when detalhe.dispute_created_at is null then 'dispute_without_created_at'
          else 'dispute_before_enable' end;
      else
        -- A disputa chegou antes da aprovação: o grant nasce no estado dela, e
        -- a aprovação que vier depois não o reativa.
        insert into app_private.entitlement_grant(
          seller_organization_id,checkout_integration_id,external_purchase_id_sha256,
          buyer_identity_user_id,catalog_product_id,status,occurred_at
        ) values (p_seller,p_integration,p_purchase,p_buyer,p_product,alvo,p_occurred);
        mudou := true;
        resultado := 'grant_created';
      end if;
    elsif grant_status = 'refunded' then
      -- Estorno vence.
      resultado := 'grant_final';
    elsif grant_status = alvo then
      resultado := case when vale then 'grant_unchanged'
        when detalhe.dispute_created_at is null then 'dispute_without_created_at'
        else 'dispute_before_enable' end;
    else
      update app_private.entitlement_grant set status=alvo,updated_at=clock_timestamp()
      where seller_organization_id=p_seller
        and checkout_integration_id=p_integration
        and external_purchase_id_sha256=p_purchase;
      mudou := true;
      resultado := 'grant_'||alvo;
    end if;
  end if;

  if mudou then
    acesso := case
      when exists(select 1 from app_private.entitlement_grant other
                  where other.seller_organization_id=p_seller
                    and other.buyer_identity_user_id=p_buyer
                    and other.catalog_product_id=p_product
                    and other.status='active') then 'active'
      when exists(select 1 from app_private.entitlement_grant other
                  where other.seller_organization_id=p_seller
                    and other.buyer_identity_user_id=p_buyer
                    and other.catalog_product_id=p_product
                    and other.status='suspended') then 'suspended'
      else 'revoked'
    end;
    update public.buyer_entitlement set status=acesso,updated_at=clock_timestamp()
    where seller_organization_id=p_seller
      and buyer_identity_user_id=p_buyer
      and catalog_product_id=p_product
      and status in ('active','suspended')
      and status<>acesso
    returning id into v_entitlement_id;
    -- Ganha depois de a aprovação ter sido pulada pela disputa: o acesso nunca
    -- existiu e nasce agora, como na aprovação.
    if acesso='active' and v_entitlement_id is null
       and not exists(select 1 from public.buyer_entitlement existente
                      where existente.seller_organization_id=p_seller
                        and existente.buyer_identity_user_id=p_buyer
                        and existente.catalog_product_id=p_product)
       and exists(select 1 from app_private.fulfillment_job aprovado
                  join public.webhook_event evento
                    on evento.seller_organization_id=aprovado.seller_organization_id
                   and evento.id=aprovado.webhook_event_id
                  where aprovado.seller_organization_id=p_seller
                    and aprovado.event_type='purchase.approved'
                    and aprovado.status='completed'
                    and aprovado.external_purchase_id_sha256=p_purchase
                    and evento.checkout_integration_id=p_integration) then
      select catalog.published_snapshot_id into v_publication
      from public.catalog_product catalog
      where catalog.seller_organization_id=p_seller and catalog.id=p_product;
      if v_publication is not null then
        insert into public.buyer_entitlement(
          seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
          publication_id,reference_key,source,status,progress_percent
        ) values (
          p_seller,gen_random_uuid(),p_buyer,p_product,v_publication,
          'billing:'||p_buyer::text||':'||p_product::text,'billing','active',0
        )
        returning id into v_entitlement_id;
      end if;
    end if;
  end if;

  update app_private.fulfillment_job set status='completed',
    lease_owner=null,lease_expires_at=null,error_code=null,
    updated_at=clock_timestamp()
  where seller_organization_id=p_seller and id=p_job;
  insert into public.audit_event(seller_organization_id,actor_identity_user_id,
    action,target_type,target_id,result,request_id,metadata)
  values(p_seller,p_worker,
    case when v_entitlement_id is null then 'commerce.fulfillment_dispute_recorded'
      else 'commerce.fulfillment_dispute_applied' end,
    'fulfillment_job',p_job,'succeeded',p_request,
    jsonb_build_object('webhook_event_id',p_webhook_event,
      'event_type',p_event_type,'reason',resultado,
      'dispute_state',estado_gravado,'dispute_effective',vale,
      'entitlement_status',acesso,
      'external_purchase_id_sha256',p_purchase));
  return jsonb_build_object('status','completed',
    'applied',v_entitlement_id is not null,'reason',resultado,
    'entitlement_id',v_entitlement_id,'entitlement_status',acesso);
end $$
"""

# Âncoras conferidas contra o texto da função (0020, com a inserção da 0049
# no ramo de aprovação, que não toca estes trechos).
_DESVIO_DE = "          -- purchase.refunded / subscription.cancelled: marca o grant desta\n"
_DESVIO_PARA = (
    "          -- Disputa de pagamento (#542, 0074): regra própria.\n"
    "          if job.event_type in ('purchase.dispute_opened','purchase.dispute_won',\n"
    "              'purchase.dispute_lost') then\n"
    "            return app_private.apply_checkout_dispute(p_worker,p_seller,p_job,\n"
    "              p_request,job.event_type,event.checkout_integration_id,\n"
    "              job.external_purchase_id_sha256,event.occurred_at,\n"
    "              job.webhook_event_id,buyer_id,mapped_product_id);\n"
    "          end if;\n"
    "\n" + _DESVIO_DE
)

_APROVACAO_DE = (
    "              status=case when app_private.entitlement_grant.status='refunded'\n"
    "                then 'refunded' else 'active' end,\n"
)
_APROVACAO_PARA = (
    "              status=case when app_private.entitlement_grant.status\n"
    "                  in ('refunded','suspended','charged_back')\n"
    "                then app_private.entitlement_grant.status else 'active' end,\n"
)

# O estorno não troca a disputa perdida por estorno.
_ESTORNO_GRANT_DE = "            status='refunded',updated_at=clock_timestamp();\n"
_ESTORNO_GRANT_PARA = (
    "            status=case when app_private.entitlement_grant.status='charged_back'\n"
    "              then 'charged_back' else 'refunded' end,\n"
    "            updated_at=clock_timestamp();\n"
)

# Estornada a compra, o acesso fica suspenso se outra compra do par está
# suspensa (a vitória dela o devolve), e revogado se não.
_ESTORNO_DE = (
    "            update public.buyer_entitlement set status='revoked',\n"
    "              updated_at=clock_timestamp()\n"
    "            where seller_organization_id=p_seller\n"
    "              and buyer_identity_user_id=buyer_id\n"
    "              and catalog_product_id=mapped_product_id\n"
    "              and status='active'\n"
    "            returning id into v_entitlement_id;\n"
)
_ESTORNO_PARA = (
    "            update public.buyer_entitlement set status=alvo_do_acesso.status,\n"
    "              updated_at=clock_timestamp()\n"
    "            from (select case when exists(\n"
    "                select 1 from app_private.entitlement_grant suspenso\n"
    "                where suspenso.seller_organization_id=p_seller\n"
    "                  and suspenso.buyer_identity_user_id=buyer_id\n"
    "                  and suspenso.catalog_product_id=mapped_product_id\n"
    "                  and suspenso.status='suspended')\n"
    "              then 'suspended' else 'revoked' end as status) alvo_do_acesso\n"
    "            where public.buyer_entitlement.seller_organization_id=p_seller\n"
    "              and public.buyer_entitlement.buyer_identity_user_id=buyer_id\n"
    "              and public.buyer_entitlement.catalog_product_id=mapped_product_id\n"
    "              and public.buyer_entitlement.status in ('active','suspended')\n"
    "              and public.buyer_entitlement.status<>alvo_do_acesso.status\n"
    "            returning public.buyer_entitlement.id into v_entitlement_id;\n"
)

_TROCAS_NA_APLICACAO: tuple[tuple[str, str], ...] = (
    (_DESVIO_DE, _DESVIO_PARA),
    (_APROVACAO_DE, _APROVACAO_PARA),
    (_ESTORNO_GRANT_DE, _ESTORNO_GRANT_PARA),
    (_ESTORNO_DE, _ESTORNO_PARA),
)

# Conciliação (0063): o evento de disputa sem job tem categoria própria.
_CONCILIA_EVENTO_DE = (
    "  select case when event.event_type='purchase.approved'\n"
    "      then 'approved_without_fulfillment' else 'refund_without_fulfillment' end,\n"
)
_CONCILIA_EVENTO_PARA = (
    "  select case when event.event_type='purchase.approved'\n"
    "      then 'approved_without_fulfillment'\n"
    f"    when event.event_type in ({_TIPOS_DE_DISPUTA})\n"
    "      then 'dispute_without_fulfillment'\n"
    "    else 'refund_without_fulfillment' end,\n"
)

# E o estado da disputa divergente do acesso, antes dos fatos por janela.
_CONCILIA_ESTADO_DE = "  -- Fato que nao muda (o evento chega uma vez): a janela pelo received_at\n"
_CONCILIA_ESTADO_PARA = """  -- Disputa (#542, 0074): compra suspensa ou perdida com o acesso ativo e
  -- nenhuma outra compra ativa do mesmo produto. Uma linha por compra.
  return query
  select 'disputed_with_access'::text, grant_row.seller_organization_id, seller.slug,
    'buyer_entitlement'::text, access.id, null::text,
    grant_row.external_purchase_id_sha256, grant_row.catalog_product_id,
    access.status, null::text, greatest(grant_row.updated_at, access.updated_at)
  from app_private.entitlement_grant grant_row
  join public.seller_organization seller on seller.id=grant_row.seller_organization_id
  join public.buyer_entitlement access
    on access.seller_organization_id=grant_row.seller_organization_id
   and access.buyer_identity_user_id=grant_row.buyer_identity_user_id
   and access.catalog_product_id=grant_row.catalog_product_id
  where grant_row.status in ('suspended','charged_back')
    and access.status='active'
    and not exists (
      select 1 from app_private.entitlement_grant other
      where other.seller_organization_id=grant_row.seller_organization_id
        and other.buyer_identity_user_id=grant_row.buyer_identity_user_id
        and other.catalog_product_id=grant_row.catalog_product_id
        and other.status='active');

  -- Acesso suspenso sem compra suspensa do mesmo produto: a suspensao perdeu
  -- o motivo e nada a desfaz sozinho.
  return query
  select 'suspended_without_dispute'::text, access.seller_organization_id, seller.slug,
    'buyer_entitlement'::text, access.id, null::text, null::text,
    access.catalog_product_id, access.status, null::text, access.updated_at
  from public.buyer_entitlement access
  join public.seller_organization seller on seller.id=access.seller_organization_id
  where access.status='suspended'
    and not exists (
      select 1 from app_private.entitlement_grant grant_row
      where grant_row.seller_organization_id=access.seller_organization_id
        and grant_row.buyer_identity_user_id=access.buyer_identity_user_id
        and grant_row.catalog_product_id=access.catalog_product_id
        and grant_row.status='suspended');

  -- Compra suspensa em loja com a regra desligada: so o desfecho da propria
  -- disputa a resolve. Uma linha por compra.
  return query
  select 'disputed_while_disabled'::text, grant_row.seller_organization_id, seller.slug,
    'entitlement_grant'::text, null::uuid, null::text,
    grant_row.external_purchase_id_sha256, grant_row.catalog_product_id,
    grant_row.status, null::text, grant_row.updated_at
  from app_private.entitlement_grant grant_row
  join public.seller_organization seller on seller.id=grant_row.seller_organization_id
  where grant_row.status='suspended'
    and coalesce((select chave.action from app_private.dispute_access_switch chave
                  where chave.seller_organization_id=grant_row.seller_organization_id
                  order by chave.created_at desc, chave.id desc limit 1),
                 'disable') <> 'enable';

  -- Evento de disputa sem a data real da disputa: gravado sem efeito no
  -- acesso; alguem confere a disputa na Stripe. Fato, pela janela.
  return query
  select 'dispute_without_created_at'::text, event.seller_organization_id, seller.slug,
    'webhook_event'::text, event.id, event.event_type, event.external_purchase_id_sha256,
    null::uuid, null::text, null::text, event.received_at
  from public.webhook_event event
  join public.seller_organization seller on seller.id=event.seller_organization_id
  join app_private.checkout_dispute_event detail
    on detail.seller_organization_id=event.seller_organization_id
   and detail.webhook_event_id=event.id
  where event.received_at >= p_since and event.received_at < p_before
    and detail.dispute_created_at is null;

  -- Fato que nao muda (o evento chega uma vez): a janela pelo received_at
"""

_TROCAS_NA_CONCILIACAO: tuple[tuple[str, str], ...] = (
    (_CONCILIA_EVENTO_DE, _CONCILIA_EVENTO_PARA),
    (_CONCILIA_ESTADO_DE, _CONCILIA_ESTADO_PARA),
)

_RECUSA_DESCIDA = """
do $migration$
begin
  if exists(select 1 from public.webhook_event
            where event_type in (__DISPUTA__))
     or exists(select 1 from app_private.entitlement_grant
               where status in ('suspended','charged_back'))
     or exists(select 1 from public.buyer_entitlement where status='suspended')
     or exists(select 1 from app_private.purchase_dispute)
     or exists(select 1 from app_private.dispute_access_switch) then
    raise exception 'ha disputa ou chave registrada; a descida apagaria decisao de acesso ou auditoria'
      using errcode='55000';
  end if;
end $migration$
"""


_TROCA_TIPOS_NAS_TABELAS_DESCIDA = _TROCA_TIPOS_NAS_TABELAS.replace("__TIPOS__", _TIPOS_DE_ANTES)


def _sobe_esquema() -> None:
    # Alterar tabelas exige o dono, que em produção pode ser o migrador; o
    # `reset role` devolve a sessão a ele só pelo tempo das trocas (padrão da 0060).
    op.execute("reset role")
    op.execute(_TROCA_TIPOS_NAS_TABELAS.replace("__TIPOS__", _TIPOS_DE_AGORA))
    op.execute(_troca_status(_GRANT, "'refunded'", _GRANT_AGORA))
    op.execute(_troca_status(_ENTITLEMENT, "'expired'", _ENTITLEMENT_AGORA))
    op.execute("set local role appsell_owner")


def _cria_tabelas() -> None:
    op.execute(_CHAVE_POR_LOJA)
    op.execute(_INDICE_DA_CHAVE)
    op.execute(_SO_INSERCAO)
    op.execute(_GATILHO_SO_INSERCAO)
    op.execute("revoke all on function app_private.dispute_access_switch_append_only() from public")
    op.execute(_DADOS_DO_EVENTO)
    op.execute(_DISPUTA_DA_COMPRA)
    for tabela in _TABELAS_NOVAS:
        for comando in _rls(tabela):
            op.execute(comando)


def _cria_funcoes() -> None:
    op.execute(_FUNCAO_DISPUTA)
    op.execute(f"revoke all on function {_APPLY_DISPUTA} from public")
    op.execute(_ACEITA.replace("__DISPUTA__", _TIPOS_DE_DISPUTA))
    op.execute(f"revoke all on function {_ACEITA_DISPUTA} from public")
    op.execute(f"grant execute on function {_ACEITA_DISPUTA} to appsell_runtime")


def upgrade() -> None:
    _sobe_esquema()
    _cria_tabelas()
    _cria_funcoes()
    op.execute(
        _TROCA_TIPOS_NAS_FUNCOES.replace("__DE__", _TIPOS_DE_ANTES).replace(
            "__PARA__", _TIPOS_DE_AGORA
        )
    )
    for de, para in _TROCAS_NA_APLICACAO:
        op.execute(_reescreve(_APPLY, de, para))
    for de, para in _TROCAS_NA_CONCILIACAO:
        op.execute(_reescreve(_RELATORIO, de, para))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, _READY_ATE_0074))
    de, para = _ready_funcao()
    op.execute(_reescreve(_BASE_DO_ACESSO, de, para))


def downgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_RECUSA_DESCIDA.replace("__DISPUTA__", _TIPOS_DE_DISPUTA))
    de, para = _ready_funcao()
    op.execute(_reescreve(_BASE_DO_ACESSO, para, de))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, _READY_ATE_0074, READY_ANTES))
    for de, para in reversed(_TROCAS_NA_CONCILIACAO):
        op.execute(_reescreve(_RELATORIO, para, de))
    for de, para in reversed(_TROCAS_NA_APLICACAO):
        op.execute(_reescreve(_APPLY, para, de))
    op.execute(
        _TROCA_TIPOS_NAS_FUNCOES.replace("__DE__", _TIPOS_DE_AGORA).replace(
            "__PARA__", _TIPOS_DE_ANTES
        )
    )
    op.execute(f"drop function {_ACEITA_DISPUTA}")
    op.execute(f"drop function {_APPLY_DISPUTA}")
    op.execute("drop table app_private.purchase_dispute")
    op.execute("drop table app_private.checkout_dispute_event")
    op.execute("drop table app_private.dispute_access_switch")
    op.execute("drop function app_private.dispute_access_switch_append_only()")
    op.execute("reset role")
    op.execute(_troca_status(_ENTITLEMENT, "'suspended'", _ENTITLEMENT_ANTES))
    op.execute(_troca_status(_GRANT, "'charged_back'", _GRANT_ANTES))
    op.execute(_TROCA_TIPOS_NAS_TABELAS_DESCIDA)
    op.execute("set local role appsell_owner")
