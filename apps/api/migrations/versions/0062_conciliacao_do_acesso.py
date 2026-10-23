"""A conciliação lê o que o dinheiro deveria ter liberado (#147).

Uma venda pode chegar ao Sell e não virar acesso sem deixar rastro: o
fulfillment morre em `dead_letter`, fica parado, ou o acesso some depois do
grant. E um estorno pode chegar e o acesso continuar. Nada disso aparece para
ninguém hoje.

Esta migration cria **uma** leitura, `app_private.checkout_reconciliation_report`.
Ela só lê (`stable`) e não devolve e-mail nem `identity_sub`: as compras aparecem
pelo `sha256` que o próprio ingress guarda. Não é concedida ao `appsell_runtime`,
porque o Sell web nunca a chama. Quem chama é o comando
`appsell_api.webhook_reconciliation`, numa transação só de leitura, com o papel
`appsell_reconciler`: `connect` no banco, `usage` em `app_private` e `execute`
nesta função, mais nada. O papel nasce no init de superusuário
(`infra/postgres/init/001_roles.sh`), porque o migrator é `nocreaterole`. Por
isso o grant abaixo só acontece se o papel já existir. Em produção, criar o
papel é mudança de permissão e depende do OK do Rodrigo.

Categorias. As de **estado** valem enquanto o problema existir, sem janela:
um job que morre depois de uma execução aparece na seguinte, e não há ponto
cego entre execuções.

| Categoria | O que significa |
|---|---|
| `fulfillment_dead_letter` | o job de entrega esgotou as tentativas |
| `fulfillment_stalled` | o job está pendente, em retry ou com lease há mais que o limite |
| `fulfillment_completed_without_grant` | `purchase.approved` concluído sem linha em `entitlement_grant` |
| `grant_without_access` | a compra tem grant ativo e o comprador não tem acesso ativo |
| `access_without_grant` | acesso `billing` ativo sem grant nenhum, nem ativo nem estornado (acesso sem compra) |
| `refunded_with_access` | a compra foi estornada, nenhum grant ativo restou e o acesso segue ativo; uma linha por compra |

As de **evento** usam a janela pelo `received_at`, que não muda: execuções que
encostam uma na outra cobrem todo evento uma vez.

| Categoria | O que significa |
|---|---|
| `approved_without_fulfillment` | `purchase.approved` aceito sem job (inclui payload v1, sem comprador) |
| `refund_without_fulfillment` | `purchase.refunded` ou `subscription.cancelled` aceito sem job: o estorno nunca foi aplicado |

Ficam de fora: o lado do dinheiro (pago no Stripe/Financial e nunca entregue),
que depende de fonte que o Sell não tem hoje e, quando existir, casa pelo mesmo
`external_purchase_id_sha256`; e o ingress rejeitado, que não deixa linha (#147,
#462).

O `downgrade` existe e só remove a leitura, sem dado a preservar. É exceção à
cadeia forward-only desde a 0059.

Revision ID: 0062_conciliacao_do_acesso
Revises: 0061_o_atraso_alcanca_o_aluno
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0062_conciliacao_do_acesso"
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
    "'0062_conciliacao_do_acesso'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
_RELATORIO = "app_private.checkout_reconciliation_report(timestamptz,timestamptz,interval)"
# Literal de propósito: o papel e a assinatura não vêm de entrada nenhuma.
_CONCEDE = """
do $migration$
begin
  if exists(select 1 from pg_roles where rolname='appsell_reconciler') then
    grant usage on schema app_private to appsell_reconciler;
    grant execute on function
      app_private.checkout_reconciliation_report(timestamptz,timestamptz,interval)
      to appsell_reconciler;
  end if;
end $migration$
"""
_RETIRA = """
do $migration$
begin
  if exists(select 1 from pg_roles where rolname='appsell_reconciler') then
    revoke execute on function
      app_private.checkout_reconciliation_report(timestamptz,timestamptz,interval)
      from appsell_reconciler;
    revoke usage on schema app_private from appsell_reconciler;
  end if;
end $migration$
"""


def _migration_anterior() -> ModuleType:
    """Lê a âncora que a `0061` deixou no `schema_ready`, em vez de copiá-la."""
    caminho = Path(__file__).with_name(f"{down_revision}.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0061", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError("a migration 0061 nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_FUNCAO = r"""
create function app_private.checkout_reconciliation_report(
  p_since timestamptz, p_before timestamptz, p_stale_after interval
) returns table(
  category text, seller_organization_id uuid, seller_slug text,
  reference_type text, reference_id uuid, event_type text,
  external_purchase_id_sha256 text, catalog_product_id uuid,
  status text, error_code text, observed_at timestamptz
) language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
#variable_conflict use_column
begin
  if p_since is null or p_before is null or p_stale_after is null
     or p_before <= p_since or p_before - p_since > interval '31 days'
     or p_stale_after < interval '1 minute' or p_stale_after > interval '7 days' then
    raise exception 'reconciliation window is invalid' using errcode='22023';
  end if;

  -- Estado atual: aparece em toda execucao enquanto nao for reparado.
  return query
  select 'fulfillment_dead_letter'::text, job.seller_organization_id, seller.slug,
    'fulfillment_job'::text, job.id, job.event_type, job.external_purchase_id_sha256,
    null::uuid, job.status, job.error_code, job.updated_at
  from app_private.fulfillment_job job
  join public.seller_organization seller on seller.id=job.seller_organization_id
  where job.status='dead_letter';

  return query
  select 'fulfillment_stalled'::text, job.seller_organization_id, seller.slug,
    'fulfillment_job'::text, job.id, job.event_type, job.external_purchase_id_sha256,
    null::uuid, job.status, job.error_code, job.updated_at
  from app_private.fulfillment_job job
  join public.seller_organization seller on seller.id=job.seller_organization_id
  where job.status in ('pending','retry','leased')
    and job.created_at < now() - p_stale_after;

  return query
  select 'fulfillment_completed_without_grant'::text, job.seller_organization_id,
    seller.slug, 'fulfillment_job'::text, job.id, job.event_type,
    job.external_purchase_id_sha256, null::uuid, job.status, null::text, job.updated_at
  from app_private.fulfillment_job job
  join public.seller_organization seller on seller.id=job.seller_organization_id
  join public.webhook_event event
    on event.seller_organization_id=job.seller_organization_id
   and event.id=job.webhook_event_id
  where job.status='completed' and job.event_type='purchase.approved'
    and not exists (
      select 1 from app_private.entitlement_grant grant_row
      where grant_row.seller_organization_id=job.seller_organization_id
        and grant_row.checkout_integration_id=event.checkout_integration_id
        and grant_row.external_purchase_id_sha256=job.external_purchase_id_sha256);

  return query
  select 'grant_without_access'::text, grant_row.seller_organization_id, seller.slug,
    'entitlement_grant'::text, access.id, null::text,
    grant_row.external_purchase_id_sha256, grant_row.catalog_product_id,
    coalesce(access.status,'missing'), null::text,
    greatest(grant_row.updated_at, access.updated_at)
  from app_private.entitlement_grant grant_row
  join public.seller_organization seller on seller.id=grant_row.seller_organization_id
  left join public.buyer_entitlement access
    on access.seller_organization_id=grant_row.seller_organization_id
   and access.buyer_identity_user_id=grant_row.buyer_identity_user_id
   and access.catalog_product_id=grant_row.catalog_product_id
  where grant_row.status='active'
    and access.status is distinct from 'active';

  return query
  select 'access_without_grant'::text, access.seller_organization_id, seller.slug,
    'buyer_entitlement'::text, access.id, null::text, null::text,
    access.catalog_product_id, access.status, null::text, access.updated_at
  from public.buyer_entitlement access
  join public.seller_organization seller on seller.id=access.seller_organization_id
  where access.source='billing' and access.status='active'
    and not exists (
      select 1 from app_private.entitlement_grant grant_row
      where grant_row.seller_organization_id=access.seller_organization_id
        and grant_row.buyer_identity_user_id=access.buyer_identity_user_id
        and grant_row.catalog_product_id=access.catalog_product_id);

  -- Uma linha por compra estornada: dois estornos do mesmo produto, duas linhas.
  return query
  select 'refunded_with_access'::text, grant_row.seller_organization_id, seller.slug,
    'buyer_entitlement'::text, access.id, null::text,
    grant_row.external_purchase_id_sha256, grant_row.catalog_product_id,
    access.status, null::text, greatest(grant_row.updated_at, access.updated_at)
  from app_private.entitlement_grant grant_row
  join public.seller_organization seller on seller.id=grant_row.seller_organization_id
  join public.buyer_entitlement access
    on access.seller_organization_id=grant_row.seller_organization_id
   and access.buyer_identity_user_id=grant_row.buyer_identity_user_id
   and access.catalog_product_id=grant_row.catalog_product_id
  where grant_row.status='refunded'
    and access.status='active'
    and not exists (
      select 1 from app_private.entitlement_grant other
      where other.seller_organization_id=grant_row.seller_organization_id
        and other.buyer_identity_user_id=grant_row.buyer_identity_user_id
        and other.catalog_product_id=grant_row.catalog_product_id
        and other.status='active');

  -- Fato que nao muda (o evento chega uma vez): a janela pelo received_at
  -- cobre tudo quando as execucoes encostam uma na outra.
  return query
  select case when event.event_type='purchase.approved'
      then 'approved_without_fulfillment' else 'refund_without_fulfillment' end,
    event.seller_organization_id, seller.slug,
    'webhook_event'::text, event.id, event.event_type, event.external_purchase_id_sha256,
    null::uuid, null::text, null::text, event.received_at
  from public.webhook_event event
  join public.seller_organization seller on seller.id=event.seller_organization_id
  where event.received_at >= p_since and event.received_at < p_before
    and not exists (
      select 1 from app_private.fulfillment_job job
      where job.seller_organization_id=event.seller_organization_id
        and job.webhook_event_id=event.id);
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


def _ready_funcao() -> tuple[str, str]:
    de = _migration_anterior()._READY_FUNCAO_PARA
    return de, de + f"            and to_regprocedure('{_RELATORIO}') is not null\n"


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_FUNCAO)
    op.execute(f"revoke all on function {_RELATORIO} from public")
    op.execute(_CONCEDE)
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0061)
            .replace("PARA", _READY_ATE_0062)
        )
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", de).replace("PARA", para))


def downgrade() -> None:
    """Só remove uma leitura: não há dado a preservar."""
    op.execute("set local role appsell_owner")
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", para).replace("PARA", de))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0062)
            .replace("PARA", _READY_ATE_0061)
        )
    op.execute(_RETIRA)
    op.execute(f"drop function {_RELATORIO}")
