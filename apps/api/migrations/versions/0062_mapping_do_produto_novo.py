"""Produto novo de loja vinculada ao Financial ganha mapping (#465).

A 0059 deu a cada loja vinculada ao Financial uma integração e um endpoint no
canal global, e mapeou **os produtos que existiam naquele dia**. Ninguém mapeia
produto criado depois: em runtime, nada insere em `external_product_mapping`.
A `create-session` manda `product.id` como produto de fulfillment, e o
`apply_checkout_fulfillment` (0020) grava o job em `dead_letter` com
`product_mapping_missing`. O comprador paga e não recebe acesso.

Esta migration:

* cria `app_private.map_financial_product()`, um trigger `after insert` em
  `catalog_product`. Se a loja está vinculada ao Financial e tem o endpoint do
  canal, o produto ganha o mapping `sha256(product.id)` na integração desse
  endpoint, exatamente como a 0059 fez;
* faz um backfill idempotente dos produtos de loja vinculada que ainda estão sem
  mapping.

Só acrescenta linhas. Nenhum mapping existente muda, e loja sem vínculo ou sem
endpoint do canal não ganha nada.

O canal é achado pela identidade que a 0059 grava, e não por idade: as
integrações `generic_hmac_v1` chamadas `'Financial checkout'` e os endpoints
delas, que copiam o mesmo `endpoint_key_sha256` e `secret_reference` do canal
global. `app_private.financial_channel()` exige exatamente **uma** identidade
ativa; se não houver nenhuma, ou houver mais de uma, levanta erro (55000) em vez
de mapear no lugar errado. Um endpoint antigo reativado, de outro adapter ou de
outro nome, não muda o canal. No trigger esse erro vira `RAISE WARNING`
(`appsell.financial_channel_unresolved`) e o produto entra sem mapping: criar
produto nunca falha por causa do canal. A migration e o backfill abortam. Loja vinculada sem endpoint do canal (vinculada
depois da 0059) não ganha mapping: é o #470, resolvido pela ativação do #382.

Não reprocessa `dead_letter` já existente. Se houver algum, ele vai para um
runbook com OK do Rodrigo.

O `downgrade` tira o trigger e as funções. Os mappings criados ficam: apagá-los
quebraria compra que já depende deles.

Revision ID: 0062_mapping_do_produto_novo
Revises: 0061_o_atraso_alcanca_o_aluno
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0062_mapping_do_produto_novo"
down_revision = "0061_o_atraso_alcanca_o_aluno"
branch_labels = None
depends_on = None

_READY_ATE_0061 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno'))"
)
_READY_ATE_0062 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
_CANAL_GLOBAL = "app_private.financial_channel()"
_CANAL = "app_private.financial_channel_integration(uuid)"
_MAPEIA = "app_private.map_financial_product()"
_TRIGGER = "catalog_product_financial_mapping"


def _migration_anterior() -> ModuleType:
    """Lê a âncora que a `0061` deixou no `schema_ready`, em vez de copiá-la."""
    caminho = Path(__file__).with_name(f"{down_revision}.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0061", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError("a migration 0061 nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A identidade do canal global é a que a 0059 grava em toda loja vinculada:
# integração generic_hmac_v1 chamada 'Financial checkout', com endpoint que copia
# o hash e o secret_reference do canal. Nada de "o mais antigo": exatamente uma
# identidade ativa, ou erro.
_FUNCAO_CANAL_GLOBAL = r"""
create function app_private.financial_channel()
returns table(
  endpoint_key_sha256 text, secret_reference text,
  revision bigint, max_clock_skew_seconds integer
) language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare identities integer;
begin
  select count(distinct (endpoint.endpoint_key_sha256, endpoint.secret_reference))
  into identities
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  where integration.adapter='generic_hmac_v1'
    and integration.display_name='Financial checkout'
    and integration.status='active' and endpoint.status='active';
  if identities <> 1 then
    raise exception 'financial channel must have exactly one identity, found %', identities
      using errcode='55000';
  end if;
  return query
  select endpoint.endpoint_key_sha256, endpoint.secret_reference,
    endpoint.revision, endpoint.max_clock_skew_seconds
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  where integration.adapter='generic_hmac_v1'
    and integration.display_name='Financial checkout'
    and integration.status='active' and endpoint.status='active'
  order by endpoint.created_at,endpoint.id
  limit 1;
end $$
"""

# A integração do canal Financial da loja: a do endpoint ativo que tem o hash do
# canal global. O unique(seller, endpoint_key_sha256) da 0059 garante no máximo um.
_FUNCAO_CANAL = r"""
create function app_private.financial_channel_integration(p_seller uuid)
returns uuid language sql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
  select endpoint.checkout_integration_id
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  join app_private.financial_channel() channel
    on channel.endpoint_key_sha256=endpoint.endpoint_key_sha256
  where endpoint.seller_organization_id=p_seller
    and endpoint.status='active' and integration.status='active'
$$
"""

_FUNCAO_MAPEIA = r"""
create function app_private.map_financial_product()
returns trigger language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare integration_id uuid;
begin
  if not exists(
    select 1 from public.seller_organization seller
    where seller.id=new.seller_organization_id and seller.status='active'
      and seller.financial_operator_id is not null
      and seller.financial_product_catalog_item_id is not null
  ) then
    return new;
  end if;
  -- Canal não resolvido (nenhum ou mais de um) NÃO bloqueia a criação de
  -- produto: o produto entra sem mapping, o aviso sai no log do Postgres e a
  -- conciliação (#459) mostra o job em dead_letter. Quem reprova alto é o
  -- readiness; a migration e o backfill abortam.
  begin
    integration_id := app_private.financial_channel_integration(new.seller_organization_id);
  exception when sqlstate '55000' then
    raise warning 'appsell.financial_channel_unresolved: product % not mapped: %',
      new.id, sqlerrm;
    return new;
  end;
  if integration_id is null then
    return new;
  end if;
  insert into public.external_product_mapping(
    seller_organization_id,checkout_integration_id,external_product_id_sha256,
    external_product_id_octets,catalog_product_id
  ) values (
    new.seller_organization_id,integration_id,
    encode(digest(convert_to(new.id::text,'UTF8'),'sha256'),'hex'),
    octet_length(new.id::text)::smallint,new.id
  ) on conflict do nothing;
  return new;
end $$
"""

_CRIA_TRIGGER = f"""
create trigger {_TRIGGER}
  after insert on public.catalog_product for each row
  execute function {_MAPEIA}
"""

_BACKFILL = r"""
insert into public.external_product_mapping(
  seller_organization_id,checkout_integration_id,external_product_id_sha256,
  external_product_id_octets,catalog_product_id
)
select product.seller_organization_id,channel.integration_id,
  encode(digest(convert_to(product.id::text,'UTF8'),'sha256'),'hex'),
  octet_length(product.id::text)::smallint,product.id
from public.seller_organization seller
cross join lateral (
  select app_private.financial_channel_integration(seller.id) as integration_id
) channel
join public.catalog_product product on product.seller_organization_id=seller.id
where seller.status='active' and seller.financial_operator_id is not null
  and seller.financial_product_catalog_item_id is not null
  and channel.integration_id is not null
on conflict do nothing
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


_READY_DO_MAPPING = (
    "            and to_regprocedure('app_private.financial_channel()') is not null\n"
    "            and to_regprocedure('app_private.financial_channel_integration(uuid)')"
    " is not null\n"
    "            and exists(select 1 from pg_catalog.pg_trigger\n"
    "              where tgrelid='public.catalog_product'::regclass\n"
    "                and tgname='catalog_product_financial_mapping'"
    " and not tgisinternal and tgenabled='O')\n"
)


def _ready_funcao() -> tuple[str, str]:
    de = _migration_anterior()._READY_FUNCAO_PARA
    return de, de + _READY_DO_MAPPING


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_FUNCAO_CANAL_GLOBAL)
    op.execute(_FUNCAO_CANAL)
    op.execute(_FUNCAO_MAPEIA)
    op.execute(f"revoke all on function {_CANAL_GLOBAL} from public")
    op.execute(f"revoke all on function {_CANAL} from public")
    op.execute(f"revoke all on function {_MAPEIA} from public")
    op.execute(_CRIA_TRIGGER)
    op.execute(_BACKFILL)
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0061)
            .replace("PARA", _READY_ATE_0062)
        )
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", de).replace("PARA", para))


def downgrade() -> None:
    """Tira o trigger e as funções; os mappings criados ficam."""
    op.execute("set local role appsell_owner")
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", para).replace("PARA", de))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0062)
            .replace("PARA", _READY_ATE_0061)
        )
    op.execute(f"drop trigger {_TRIGGER} on public.catalog_product")
    op.execute(f"drop function {_MAPEIA}")
    op.execute(f"drop function {_CANAL}")
    op.execute(f"drop function {_CANAL_GLOBAL}")
