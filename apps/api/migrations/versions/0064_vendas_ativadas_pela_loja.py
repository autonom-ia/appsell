"""A loja ativa as próprias vendas pelo Financial, sem passo manual (#382).

Hoje uma loja só vende depois que a operação roda
`configure_seller_financial` com a URL de banco de provisionamento, e só recebe
a entrega da compra se a 0059 tiver criado a integração e o endpoint dela no
canal global. Loja nova não passou pela 0059: fica sem endpoint e sem mapping,
e a compra morreria em `dead_letter`.

Esta migration cria duas funções, chamadas pela API com o papel do vendedor:

* `app_private.resolve_seller_financial_activation(uuid)`, só leitura
  (`commerce.payout.read`): se a loja já tem o vínculo, se a conta de
  recebimentos está pronta (`charges_enabled`) e se existe o canal global.
* `app_private.activate_seller_financial(uuid,uuid,uuid,boolean,uuid)`
  (`commerce.payout.manage`): grava o par do Financial, cria a integração e o
  endpoint da loja no canal global, mapeia os produtos que já existem e audita.
  Tudo numa transação. O canal é `app_private.financial_channel()` (0062): a
  identidade única que a 0059 grava, nunca "o endpoint mais antigo". Sem canal,
  ou com mais de um, a ativação levanta 55000 e nada é gravado.

O par (operador e item de catálogo) é **global**: vem da env da API
(`APPSELL_FINANCIAL_SHARED_OPERATOR_ID`, `APPSELL_FINANCIAL_SHARED_CATALOG_ITEM_ID`),
nunca do navegador. O produto do Sell segue no snapshot do checkout.

Loja que **já tem vínculo** nunca tem o par trocado, mesmo que seja diferente do
global (é o caso da Hub2You, gravado à mão). Ela só é `already_active` se também
tiver o endpoint ativo no canal e todos os produtos mapeados; se faltar endpoint
ou mapping, a ativação **repara** (cria só o que falta, idempotente), audita
`seller_financial.repaired` e devolve `repaired`. Endpoint do canal desativado
não é reativado: `channel_disabled`, sem escrita. As outras recusas também não
escrevem: `payout_account_not_ready` (pré-condição da #439, só para vínculo
novo e só quando a API pede) e `seller_inactive`. A função confere no banco que
o ator é `seller_owner` da loja (42501), além da API.

A leitura devolve também `endpoint_state` (`active`, `disabled`, `missing`) e
quantos produtos estão sem mapping, para a tela nunca dizer "vendas ativas"
enquanto a compra iria para `dead_letter`.

Resolve o #470: loja vinculada depois da 0059 ganha aqui o endpoint do canal.

Quem pode ativar (flag por loja, desligada para a Hub2You) é decidido na API.
Esta migration não liga nada sozinha.

O `downgrade` tira as funções e o readiness. Os vínculos, endpoints e mappings
criados ficam: apagá-los quebraria compra que já depende deles.

Revision ID: 0064_vendas_ativadas_pela_loja
Revises: 0063_conciliacao_do_acesso
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0064_vendas_ativadas_pela_loja"
down_revision = "0063_conciliacao_do_acesso"
branch_labels = None
depends_on = None

_READY_ATE_0063 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso'))"
)
_READY_ATE_0064 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
_SITUACAO = "app_private.resolve_seller_financial_activation(uuid)"
_ATIVA = "app_private.activate_seller_financial(uuid,uuid,uuid,boolean,uuid)"


def _migration_anterior() -> ModuleType:
    """Lê a âncora que a `0063` deixou no `schema_ready`, em vez de copiá-la."""
    caminho = Path(__file__).with_name(f"{down_revision}.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0063", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError("a migration 0063 nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_FUNCAO_SITUACAO = r"""
create function app_private.resolve_seller_financial_activation(p_seller uuid)
returns table(
  bound boolean, payout_ready boolean, channel_ready boolean,
  endpoint_state text, unmapped_products integer
)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  v_channel record;
  v_channel_found boolean := false;
  v_integration uuid;
  v_endpoint_state text := 'missing';
  v_unmapped integer := 0;
begin
  if app_private.context_text('capability') is distinct from 'commerce.payout.read'
    or p_seller is null
    or app_private.context_uuid('seller_organization_id') is distinct from p_seller
  then
    raise exception 'seller financial activation read denied' using errcode='42501';
  end if;
  -- A leitura não levanta: canal ausente ou ambíguo vira "bloqueado" na tela.
  begin
    select identity.endpoint_key_sha256 into v_channel
    from app_private.financial_channel() identity;
    v_channel_found := true;
  exception when sqlstate '55000' then
    v_channel_found := false;
  end;
  if v_channel_found then
    select endpoint.checkout_integration_id,
      case when endpoint.status='active' and integration.status='active'
        then 'active' else 'disabled' end
    into v_integration, v_endpoint_state
    from public.webhook_endpoint endpoint
    join public.checkout_integration integration
      on integration.seller_organization_id=endpoint.seller_organization_id
     and integration.id=endpoint.checkout_integration_id
    where endpoint.seller_organization_id=p_seller
      and endpoint.endpoint_key_sha256=v_channel.endpoint_key_sha256;
    if not found then
      v_endpoint_state := 'missing';
    elsif v_endpoint_state='active' then
      select count(*) into v_unmapped
      from public.catalog_product product
      where product.seller_organization_id=p_seller
        and not exists(
          select 1 from public.external_product_mapping mapping
          where mapping.seller_organization_id=p_seller
            and mapping.checkout_integration_id=v_integration
            and mapping.catalog_product_id=product.id);
    end if;
  end if;
  return query
  select seller.financial_operator_id is not null,
    exists(
      select 1 from public.seller_payout_account account
      where account.seller_organization_id=seller.id and account.charges_enabled),
    v_channel_found, v_endpoint_state, v_unmapped
  from public.seller_organization seller
  where seller.id=p_seller and seller.status='active';
end $$
"""

_FUNCAO_ATIVA = r"""
create function app_private.activate_seller_financial(
  p_seller uuid, p_operator uuid, p_catalog_item uuid,
  p_require_payout_ready boolean, p_request_id uuid
)
returns text language plpgsql volatile security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  v_seller record;
  v_channel record;
  v_integration uuid;
  v_endpoint_active boolean;
  v_pair_written boolean := false;
  v_endpoint_created boolean := false;
  v_products_mapped integer := 0;
begin
  if app_private.context_text('capability') is distinct from 'commerce.payout.manage'
    or p_seller is null
    or app_private.context_uuid('seller_organization_id') is distinct from p_seller
    or p_operator is null or p_catalog_item is null
    or p_require_payout_ready is null or p_request_id is null
  then
    raise exception 'seller financial activation denied' using errcode='42501';
  end if;
  -- Defesa em profundidade: a API já exige seller_owner; o banco confere de novo.
  if app_private.resolve_seller_membership_role(p_seller) is distinct from 'seller_owner' then
    raise exception 'seller financial activation requires the owner' using errcode='42501';
  end if;
  -- Serializa por loja antes de ler: duas ativações simultâneas não podem
  -- criar dois endpoints nem gravar o vínculo duas vezes.
  perform pg_advisory_xact_lock(
    hashtextextended('appsell.seller_financial_activation:' || p_seller::text, 0));

  select seller.id, seller.status, seller.financial_operator_id,
    seller.financial_product_catalog_item_id
  into v_seller
  from public.seller_organization seller
  where seller.id=p_seller
  for update;
  if not found or v_seller.status <> 'active' then
    return 'seller_inactive';
  end if;
  -- A pré-condição da #439 vale para vínculo novo; reparar um vínculo que já
  -- existe não depende dela.
  if v_seller.financial_operator_id is null and p_require_payout_ready and not exists(
    select 1 from public.seller_payout_account account
    where account.seller_organization_id=p_seller and account.charges_enabled
  ) then
    return 'payout_account_not_ready';
  end if;

  -- O canal global pela identidade da 0062; zero ou mais de um levanta 55000
  -- antes de qualquer escrita.
  select identity.endpoint_key_sha256, identity.secret_reference,
    identity.revision, identity.max_clock_skew_seconds
  into v_channel
  from app_private.financial_channel() identity;

  select endpoint.checkout_integration_id,
    endpoint.status='active' and integration.status='active'
  into v_integration, v_endpoint_active
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  where endpoint.seller_organization_id=p_seller
    and endpoint.endpoint_key_sha256=v_channel.endpoint_key_sha256;
  -- Endpoint desativado é decisão de alguém: não se reativa por aqui.
  if found and not v_endpoint_active then
    return 'channel_disabled';
  end if;

  -- Vínculo existente (a Hub2You, por exemplo) não é trocado, mesmo que o par
  -- seja diferente do global; só se completa o que falta.
  if v_seller.financial_operator_id is null then
    update public.seller_organization
    set financial_operator_id=p_operator,
        financial_product_catalog_item_id=p_catalog_item,
        updated_at=clock_timestamp()
    where id=p_seller;
    v_pair_written := true;
  end if;

  if v_integration is null then
    v_integration := gen_random_uuid();
    insert into public.checkout_integration(
      seller_organization_id,id,adapter,display_name,status
    ) values(p_seller,v_integration,'generic_hmac_v1','Financial checkout','active');
    insert into public.webhook_endpoint(
      seller_organization_id,checkout_integration_id,endpoint_key_sha256,
      secret_reference,revision,max_clock_skew_seconds,status
    ) values(
      p_seller,v_integration,v_channel.endpoint_key_sha256,
      v_channel.secret_reference,v_channel.revision,v_channel.max_clock_skew_seconds,'active'
    );
    v_endpoint_created := true;
  end if;

  insert into public.external_product_mapping(
    seller_organization_id,checkout_integration_id,external_product_id_sha256,
    external_product_id_octets,catalog_product_id
  )
  select product.seller_organization_id,v_integration,
    encode(digest(convert_to(product.id::text,'UTF8'),'sha256'),'hex'),
    octet_length(product.id::text)::smallint,product.id
  from public.catalog_product product
  where product.seller_organization_id=p_seller
  on conflict do nothing;
  get diagnostics v_products_mapped = row_count;

  if not v_pair_written and not v_endpoint_created and v_products_mapped=0 then
    return 'already_active';
  end if;

  insert into public.audit_event(
    seller_organization_id, actor_identity_user_id, action, target_type,
    target_id, result, request_id, metadata
  ) values (
    p_seller, app_private.context_uuid('identity_user_id'),
    case when v_pair_written then 'seller_financial.activated'
      else 'seller_financial.repaired' end,
    'seller_organization', p_seller, 'succeeded', p_request_id,
    jsonb_build_object(
      'financial_operator_id', coalesce(v_seller.financial_operator_id, p_operator),
      'financial_product_catalog_item_id',
        coalesce(v_seller.financial_product_catalog_item_id, p_catalog_item),
      'pair_written', v_pair_written,
      'endpoint_created', v_endpoint_created,
      'products_mapped', v_products_mapped,
      'payout_ready_required', p_require_payout_ready
    )
  );
  return case when v_pair_written then 'activated' else 'repaired' end;
end $$
"""

_REESCREVE = """
do $migration$
declare definicao text; reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao)-length(replace(definicao,$de$DE$de$,''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO nao continha a ancora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao,$de$DE$de$,$para$PARA$para$);
  execute reescrita;
end $migration$
"""

_READY_DA_ATIVACAO = (
    "            and to_regprocedure("
    "'app_private.resolve_seller_financial_activation(uuid)') is not null\n"
    "            and to_regprocedure("
    "'app_private.activate_seller_financial(uuid,uuid,uuid,boolean,uuid)') is not null\n"
)


def _ready_funcao() -> tuple[str, str]:
    de = _migration_anterior()._ready_funcao()[1]
    return de, de + _READY_DA_ATIVACAO


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_FUNCAO_SITUACAO)
    op.execute(_FUNCAO_ATIVA)
    for funcao in (_SITUACAO, _ATIVA):
        op.execute(f"revoke all on function {funcao} from public")
        op.execute(f"grant execute on function {funcao} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0063)
            .replace("PARA", _READY_ATE_0064)
        )
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", de).replace("PARA", para))


def downgrade() -> None:
    """Tira as funções; vínculos, endpoints e mappings criados ficam."""
    op.execute("set local role appsell_owner")
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", para).replace("PARA", de))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0064)
            .replace("PARA", _READY_ATE_0063)
        )
    op.execute(f"drop function {_ATIVA}")
    op.execute(f"drop function {_SITUACAO}")
