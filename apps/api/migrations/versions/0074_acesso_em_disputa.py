"""Acesso em compra com disputa de pagamento (#542).

O Financial passa a avisar o Sell da disputa de pagamento de uma venda pelo
mesmo caminho do estorno: um evento assinado entra pelo ingresso de webhook,
vira `fulfillment_job` e é aplicado pelo worker, com lease, fence e retomada.
São três eventos novos:

* `purchase.dispute_opened` — disputa aberta;
* `purchase.dispute_won` — a loja ganhou;
* `purchase.dispute_lost` — a loja perdeu.

O que muda no banco:

* as checagens de tipo de evento de `webhook_event`, `fulfillment_job` e das
  funções de ingresso passam a aceitar os três tipos;
* `entitlement_grant.status` ganha `suspended` (disputa aberta) e
  `charged_back` (disputa perdida, distinto de `refunded`, que segue sendo só
  estorno), e o grant guarda o último estado de disputa e o momento dele;
* `buyer_entitlement.status` ganha `suspended`. Quem filtra `status='active'`
  já nega o acesso sem mudança;
* `app_private.dispute_access_switch` liga a regra loja por loja. É um
  histórico só de inserção (quem, quando, por quê): a última linha da loja
  decide, e `disable` desliga sem apagar quem ligou. Vazia, a regra não vale
  para ninguém: o evento é aceito, o job é concluído e nada muda no acesso.
  Fica no banco, e não em env, porque a regra é decidida dentro do SQL, sob o
  lock da compra; só `apply_checkout_dispute` lê a tabela;
* `app_private.apply_checkout_dispute` aplica a regra. A função de
  fulfillment (`apply_checkout_fulfillment`) chama essa função para os três
  tipos novos, depois de já ter validado o lease e tomado os dois locks da
  compra e do par (comprador, produto).

A regra, por compra:

* aberta: o grant vira `suspended`;
* ganha: o grant volta a `active`;
* perdida: o grant vira `charged_back`, que é final;
* estorno vence: `refunded` não muda por disputa, e um estorno sobre um
  grant suspenso revoga o acesso;
* ordem trocada: vale o evento mais recente pelo `occurred_at`. Uma abertura
  que chega depois de um desfecho mais novo é ignorada; a perdida vale
  sempre;
* `purchase.approved` repetido não reativa um grant suspenso nem perdido.

O acesso do par (comprador, produto) é recalculado depois de cada mudança:
`active` se algum grant está ativo; senão `suspended` se algum está suspenso;
senão `revoked`. Só muda um acesso que estava `active` ou `suspended`; um acesso
expirado ou revogado por outro motivo não volta por disputa.

A função de fulfillment é reescrita pelo padrão da 0049 (`pg_get_functiondef`
com `replace` ancorado, cada âncora exatamente uma vez). A assinatura não muda,
então a api em produção segue funcionando entre a migration e o deploy.

A descida recusa enquanto existir evento de disputa, grant suspenso ou
perdido, ou acesso suspenso: esse estado não tem como voltar ao esquema
anterior sem decidir o acesso de alguém.
"""

from __future__ import annotations

from alembic import op

revision = "0074_acesso_em_disputa"
down_revision = "0065_dono_da_conta_payout"
branch_labels = None
depends_on = None

_APPLY = "app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text)"
_APPLY_DISPUTA = "app_private.apply_checkout_dispute(uuid,uuid,uuid,uuid,text,uuid,text,timestamptz,uuid,uuid,uuid)"

_TIPOS_DE_ANTES = "'purchase.approved','purchase.refunded','subscription.cancelled'"
_TIPOS_DE_DISPUTA = "'purchase.dispute_opened','purchase.dispute_won','purchase.dispute_lost'"
_TIPOS_DE_AGORA = _TIPOS_DE_ANTES + "," + _TIPOS_DE_DISPUTA

_READY_ATE_0065 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout'))"
)
_READY_ATE_0074 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0074_acesso_em_disputa'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)

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
# existir (accept_checkout_webhook); cada uma é reescrita com a lista nova.
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

_COLUNAS_DO_GRANT = """
alter table app_private.entitlement_grant
  add column dispute_state text check (dispute_state in ('opened','won','lost')),
  add column dispute_occurred_at timestamptz,
  add constraint entitlement_grant_dispute_pair_check
    check ((dispute_state is null) = (dispute_occurred_at is null))
"""

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
language plpgsql set search_path=pg_catalog as $$
begin
  raise exception 'dispute_access_switch is append-only' using errcode='55000';
end $$
"""

_GATILHO_SO_INSERCAO = """
create trigger dispute_access_switch_append_only
  before update or delete on app_private.dispute_access_switch
  for each row execute function app_private.dispute_access_switch_append_only()
"""

_FUNCAO_DISPUTA = """
create function app_private.apply_checkout_dispute(
  p_worker uuid,p_seller uuid,p_job uuid,p_request uuid,p_event_type text,
  p_integration uuid,p_purchase text,p_occurred timestamptz,p_webhook_event uuid,
  p_buyer uuid,p_product uuid
) returns jsonb language plpgsql security definer
set search_path=pg_catalog,app_private,public as $$
declare
  ledger record; estado text; novo_status text; resultado text;
  acesso text; v_entitlement_id uuid; mudou boolean := false;
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
  novo_status := case estado
    when 'opened' then 'suspended'
    when 'lost' then 'charged_back'
    else 'active'
  end;

  if coalesce((select chave.action from app_private.dispute_access_switch chave
                where chave.seller_organization_id=p_seller
                order by chave.created_at desc, chave.id desc limit 1),
              'disable') <> 'enable' then
    resultado := 'dispute_access_disabled';
  else
    select grant_row.status,grant_row.dispute_occurred_at into ledger
    from app_private.entitlement_grant grant_row
    where grant_row.seller_organization_id=p_seller
      and grant_row.checkout_integration_id=p_integration
      and grant_row.external_purchase_id_sha256=p_purchase
    for update;
    if not found then
      -- A disputa chegou antes da aprovação: o grant nasce no estado dela, e
      -- a aprovação que vier depois não o reativa.
      insert into app_private.entitlement_grant(
        seller_organization_id,checkout_integration_id,external_purchase_id_sha256,
        buyer_identity_user_id,catalog_product_id,status,occurred_at,
        dispute_state,dispute_occurred_at
      ) values (
        p_seller,p_integration,p_purchase,p_buyer,p_product,novo_status,p_occurred,
        estado,p_occurred
      );
      mudou := true;
      resultado := 'grant_created';
    elsif ledger.status in ('refunded','charged_back') then
      -- Estorno vence, e a disputa perdida é final.
      resultado := 'grant_final';
    elsif estado <> 'lost' and ledger.dispute_occurred_at is not null
          and (p_occurred < ledger.dispute_occurred_at
               or (estado='opened' and p_occurred = ledger.dispute_occurred_at)) then
      -- Ordem trocada: já existe um estado de disputa mais novo.
      resultado := 'stale_event';
    else
      update app_private.entitlement_grant set
        status=novo_status,dispute_state=estado,dispute_occurred_at=p_occurred,
        updated_at=clock_timestamp()
      where seller_organization_id=p_seller
        and checkout_integration_id=p_integration
        and external_purchase_id_sha256=p_purchase;
      mudou := ledger.status is distinct from novo_status;
      resultado := 'grant_'||novo_status;
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

_ESTORNO_DE = "              and status='active'\n            returning id into v_entitlement_id;\n"
_ESTORNO_PARA = (
    "              and status in ('active','suspended')\n"
    "            returning id into v_entitlement_id;\n"
)

_TROCAS_NA_APLICACAO: tuple[tuple[str, str], ...] = (
    (_DESVIO_DE, _DESVIO_PARA),
    (_APROVACAO_DE, _APROVACAO_PARA),
    (_ESTORNO_DE, _ESTORNO_PARA),
)

_RECUSA_DESCIDA = """
do $migration$
begin
  if exists(select 1 from public.webhook_event
            where event_type in (__DISPUTA__))
     or exists(select 1 from app_private.entitlement_grant
               where status in ('suspended','charged_back') or dispute_state is not null)
     or exists(select 1 from public.buyer_entitlement where status='suspended')
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
    op.execute(_COLUNAS_DO_GRANT)
    op.execute("set local role appsell_owner")


def upgrade() -> None:
    _sobe_esquema()
    op.execute(_CHAVE_POR_LOJA)
    op.execute(_INDICE_DA_CHAVE)
    op.execute(_SO_INSERCAO)
    op.execute(_GATILHO_SO_INSERCAO)
    op.execute("revoke all on table app_private.dispute_access_switch from public,appsell_runtime")
    op.execute("revoke all on function app_private.dispute_access_switch_append_only() from public")
    op.execute(_FUNCAO_DISPUTA)
    op.execute(f"revoke all on function {_APPLY_DISPUTA} from public")
    op.execute(
        _TROCA_TIPOS_NAS_FUNCOES.replace("__DE__", _TIPOS_DE_ANTES).replace(
            "__PARA__", _TIPOS_DE_AGORA
        )
    )
    for de, para in _TROCAS_NA_APLICACAO:
        op.execute(_reescreve(_APPLY, de, para))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, _READY_ATE_0065, _READY_ATE_0074))


def downgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_RECUSA_DESCIDA.replace("__DISPUTA__", _TIPOS_DE_DISPUTA))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, _READY_ATE_0074, _READY_ATE_0065))
    for de, para in reversed(_TROCAS_NA_APLICACAO):
        op.execute(_reescreve(_APPLY, para, de))
    op.execute(
        _TROCA_TIPOS_NAS_FUNCOES.replace("__DE__", _TIPOS_DE_AGORA).replace(
            "__PARA__", _TIPOS_DE_ANTES
        )
    )
    op.execute(f"drop function {_APPLY_DISPUTA}")
    op.execute("drop table app_private.dispute_access_switch")
    op.execute("drop function app_private.dispute_access_switch_append_only()")
    op.execute("reset role")
    op.execute(
        "alter table app_private.entitlement_grant "
        "drop constraint entitlement_grant_dispute_pair_check, "
        "drop column dispute_occurred_at, drop column dispute_state"
    )
    op.execute(_troca_status(_ENTITLEMENT, "'suspended'", _ENTITLEMENT_ANTES))
    op.execute(_troca_status(_GRANT, "'charged_back'", _GRANT_ANTES))
    op.execute(_TROCA_TIPOS_NAS_TABELAS_DESCIDA)
    op.execute("set local role appsell_owner")
