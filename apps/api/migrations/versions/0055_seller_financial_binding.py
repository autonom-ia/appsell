"""Vincula cada seller ao operador e ao produto analitico do Financial.

Os dois UUIDs formam um par: o produto determina o operador no Financial e o
operador enviado pelo Sell funciona como uma segunda trava contra mistura de
credenciais entre empresas. Campos nulos preservam sellers ainda nao migrados.
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0055_seller_financial_binding"
down_revision = "0054_banner_da_loja"
branch_labels = None
depends_on = None


def _module_0054() -> ModuleType:
    path = Path(__file__).with_name("0054_banner_da_loja.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0054", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load migration 0054")
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _storefront_0054() -> str:
    m54 = _module_0054()
    m52 = m54._modulo_0052()
    return m54._com_banner(m52._VITRINE, m54._VITRINE_LOGO, m54._VITRINE_BANNER)


_SELLER_ANCHOR = """  return jsonb_build_object(
    'schema_version',1,
    'seller',jsonb_build_object(
      'id',seller.id,'slug',seller.slug,'name',seller.display_name
    ) || marca,
"""
_SELLER_WITH_FINANCIAL = """  if seller.financial_operator_id is not null then
    marca := marca || jsonb_build_object(
      'financial_operator_id',seller.financial_operator_id,
      'financial_product_catalog_item_id',seller.financial_product_catalog_item_id
    );
  end if;

  return jsonb_build_object(
    'schema_version',1,
    'seller',jsonb_build_object(
      'id',seller.id,'slug',seller.slug,'name',seller.display_name
    ) || marca,
"""
_READY_0054 = "'0053_dono_das_funcoes_payout','0054_banner_da_loja'))"
_READY_0055 = (
    "'0053_dono_das_funcoes_payout','0054_banner_da_loja','0055_seller_financial_binding'))"
)
_READY_ANCHOR = (
    "            and to_regprocedure("
    "'app_private.set_seller_image(uuid,uuid,text,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)'"
    ") is not null\n"
)
_READY_COLUMNS = _READY_ANCHOR + (  # noqa: S608 - static schema readiness SQL
    "            and exists (select 1 from pg_attribute where attrelid="
    "'public.seller_organization'::regclass and attname='financial_operator_id' "
    "and not attisdropped)\n"
    "            and exists (select 1 from pg_attribute where attrelid="
    "'public.seller_organization'::regclass "
    "and attname='financial_product_catalog_item_id' and not attisdropped)\n"
)


def _replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"migration anchor did not occur exactly once: {old!r}")
    return source.replace(old, new, 1)


def _rewrite(function: str, old: str, new: str) -> None:
    escaped_old = old.replace("'", "''")
    escaped_new = new.replace("'", "''")
    op.execute(
        f"""
        do $migration$
        declare definition text;
        begin
          definition := pg_get_functiondef('{function}'::regprocedure);
          if (length(definition)-length(replace(definition,'{escaped_old}',''))) /
             length('{escaped_old}') <> 1 then
            raise exception 'schema readiness anchor mismatch' using errcode='55000';
          end if;
          execute replace(definition,'{escaped_old}','{escaped_new}');
        end $migration$;
        """
    )


def upgrade() -> None:
    op.execute("reset role")
    op.execute(
        """
        alter table public.seller_organization
          add column financial_operator_id uuid,
          add column financial_product_catalog_item_id uuid,
          add constraint seller_organization_financial_binding_complete check (
            (financial_operator_id is null and financial_product_catalog_item_id is null)
            or
            (financial_operator_id is not null and financial_product_catalog_item_id is not null)
          )
        """
    )
    op.execute("set local role appsell_owner")
    op.execute(_replace_once(_storefront_0054(), _SELLER_ANCHOR, _SELLER_WITH_FINANCIAL))
    for function in (
        "app_private.schema_ready_base_0016()",
        "app_private.schema_ready_base_0017()",
    ):
        _rewrite(function, _READY_0054, _READY_0055)
    _rewrite("app_private.schema_ready()", _READY_ANCHOR, _READY_COLUMNS)


def downgrade() -> None:
    _rewrite("app_private.schema_ready()", _READY_COLUMNS, _READY_ANCHOR)
    for function in (
        "app_private.schema_ready_base_0016()",
        "app_private.schema_ready_base_0017()",
    ):
        _rewrite(function, _READY_0055, _READY_0054)
    op.execute(_storefront_0054())
    op.execute("reset role")
    op.execute(
        """
        alter table public.seller_organization
          drop constraint seller_organization_financial_binding_complete,
          drop column financial_operator_id,
          drop column financial_product_catalog_item_id
        """
    )
    op.execute("set local role appsell_owner")
