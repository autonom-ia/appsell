"""Permite que um canal Financial entregue compras para varios sellers.

O URL e o segredo continuam globais. Cada seller vinculado ao Financial ganha
uma integracao e um endpoint internos, com o mesmo hash e secret_reference, de
modo que eventos e mapeamentos de produto permanecam isolados pelo seller.

Revision ID: 0059_canal_financial_global
Revises: 0058_capa_na_biblioteca
"""

from __future__ import annotations

from alembic import op

revision = "0059_canal_financial_global"
down_revision = "0058_capa_na_biblioteca"
branch_labels = None
depends_on = None

_READY_ATE_0058 = "'0057_banner_na_vitrine','0058_capa_na_biblioteca'))"
_READY_ATE_0059 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_CONTRATO_WEBHOOK = "app_private.schema_ready_base_0016()"
_READY_FUNCTION_ANCHOR = (
    "            and to_regprocedure('app_private.resolve_webhook_endpoint(text)') is not null\n"
)
_READY_FUNCTIONS = _READY_FUNCTION_ANCHOR + (
    "            and to_regprocedure('app_private.resolve_webhook_endpoint(text,uuid)') is not null\n"
)
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

_RESOLVE_LEGACY = r"""
create or replace function app_private.resolve_webhook_endpoint(p_endpoint_key_sha256 text)
returns table(
  seller_organization_id uuid,webhook_endpoint_id uuid,
  checkout_integration_id uuid,adapter text,secret_reference text,
  endpoint_revision bigint,max_clock_skew_seconds integer
) language plpgsql stable security definer
set search_path=pg_catalog,app_private,public as $$
begin
  if app_private.context_text('capability') is distinct from 'commerce.webhook.ingest'
    or app_private.context_uuid('seller_organization_id') is not null
    or p_endpoint_key_sha256 is null
    or p_endpoint_key_sha256 !~ '^[0-9a-f]{64}$' then
    return;
  end if;
  return query
  select endpoint.seller_organization_id,endpoint.id,integration.id,
    integration.adapter,endpoint.secret_reference,endpoint.revision,
    endpoint.max_clock_skew_seconds
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  join public.seller_organization seller on seller.id=endpoint.seller_organization_id
  where endpoint.endpoint_key_sha256=p_endpoint_key_sha256
    and endpoint.status='active' and integration.status='active' and seller.status='active'
  order by endpoint.created_at,endpoint.seller_organization_id,endpoint.id
  limit 1;
end $$
"""

_RESOLVE_SELLER = r"""
create function app_private.resolve_webhook_endpoint(
  p_endpoint_key_sha256 text,p_seller_organization_id uuid
)
returns table(
  seller_organization_id uuid,webhook_endpoint_id uuid,
  checkout_integration_id uuid,adapter text,secret_reference text,
  endpoint_revision bigint,max_clock_skew_seconds integer
) language plpgsql stable security definer
set search_path=pg_catalog,app_private,public as $$
begin
  if app_private.context_text('capability') is distinct from 'commerce.webhook.ingest'
    or app_private.context_uuid('seller_organization_id') is not null
    or p_endpoint_key_sha256 is null
    or p_endpoint_key_sha256 !~ '^[0-9a-f]{64}$'
    or p_seller_organization_id is null then
    return;
  end if;
  return query
  select endpoint.seller_organization_id,endpoint.id,integration.id,
    integration.adapter,endpoint.secret_reference,endpoint.revision,
    endpoint.max_clock_skew_seconds
  from public.webhook_endpoint endpoint
  join public.checkout_integration integration
    on integration.seller_organization_id=endpoint.seller_organization_id
   and integration.id=endpoint.checkout_integration_id
  join public.seller_organization seller on seller.id=endpoint.seller_organization_id
  where endpoint.endpoint_key_sha256=p_endpoint_key_sha256
    and endpoint.seller_organization_id=p_seller_organization_id
    and endpoint.status='active' and integration.status='active' and seller.status='active';
end $$
"""


def upgrade() -> None:
    op.execute("reset role")
    op.execute(
        "alter table public.webhook_endpoint "
        "drop constraint webhook_endpoint_endpoint_key_sha256_key, "
        "drop constraint webhook_endpoint_secret_reference_key"
    )
    op.execute(
        "alter table public.webhook_endpoint "
        "add constraint webhook_endpoint_seller_endpoint_key_unique "
        "unique(seller_organization_id,endpoint_key_sha256), "
        "add constraint webhook_endpoint_seller_secret_reference_unique "
        "unique(seller_organization_id,secret_reference)"
    )
    op.execute("set local role appsell_owner")
    op.execute(
        r"""
        do $migration$
        declare channel record; target record; integration_id uuid;
        begin
          select endpoint.endpoint_key_sha256,endpoint.secret_reference,
            endpoint.revision,endpoint.max_clock_skew_seconds
          into channel
          from public.webhook_endpoint endpoint
          join public.checkout_integration integration
            on integration.seller_organization_id=endpoint.seller_organization_id
           and integration.id=endpoint.checkout_integration_id
          where endpoint.status='active' and integration.status='active'
          order by endpoint.created_at,endpoint.seller_organization_id,endpoint.id limit 1;
          if not found then return; end if;

          for target in
            select seller.id from public.seller_organization seller
            where seller.status='active' and seller.financial_operator_id is not null
              and seller.financial_product_catalog_item_id is not null
          loop
            select endpoint.checkout_integration_id into integration_id
            from public.webhook_endpoint endpoint
            where endpoint.seller_organization_id=target.id
              and endpoint.endpoint_key_sha256=channel.endpoint_key_sha256;
            if not found then
              integration_id := gen_random_uuid();
              insert into public.checkout_integration(
                seller_organization_id,id,adapter,display_name,status
              ) values(target.id,integration_id,'generic_hmac_v1','Financial checkout','active');
              insert into public.webhook_endpoint(
                seller_organization_id,checkout_integration_id,endpoint_key_sha256,
                secret_reference,revision,max_clock_skew_seconds,status
              ) values(
                target.id,integration_id,channel.endpoint_key_sha256,
                channel.secret_reference,channel.revision,channel.max_clock_skew_seconds,'active'
              );
            end if;

            insert into public.external_product_mapping(
              seller_organization_id,checkout_integration_id,external_product_id_sha256,
              external_product_id_octets,catalog_product_id
            )
            select product.seller_organization_id,integration_id,
              encode(digest(convert_to(product.id::text,'UTF8'),'sha256'),'hex'),
              octet_length(product.id::text)::smallint,product.id
            from public.catalog_product product
            where product.seller_organization_id=target.id
            on conflict do nothing;
          end loop;
        end $migration$
        """
    )
    op.execute(_RESOLVE_LEGACY)
    op.execute(_RESOLVE_SELLER)
    op.execute("revoke all on function app_private.resolve_webhook_endpoint(text,uuid) from public")
    op.execute("grant execute on function app_private.resolve_webhook_endpoint(text,uuid) to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0058)
            .replace("PARA", _READY_ATE_0059)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _BASE_DO_CONTRATO_WEBHOOK)
        .replace("DE", _READY_FUNCTION_ANCHOR)
        .replace("PARA", _READY_FUNCTIONS)
    )


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
