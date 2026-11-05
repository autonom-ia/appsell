"""Um produto passa a poder ter mais de uma oferta: fase 2 de várias ofertas (#553).

A fase 1 (0067) deu a toda oferta a marca de padrão e fez a vitrine, o acesso
gratuito e a publicação lerem só a padrão. Esta fase abre a segunda oferta:

- `drop constraint offer_seller_product_key`. As FKs apontam para a unique de
  três colunas, que fica.
- O default de `offer.is_default` **continua `true`**. O insert legado de
  `update_product_course_offer_draft` (primeira oferta do produto) segue
  criando a padrão, e uma segunda tentativa por esse caminho bate no índice
  parcial `offer_one_default_per_product` (23505). A recusa é do banco, não de
  código de função.
- `app_private.create_product_offer`: cria uma oferta **não padrão**, em
  rascunho. Trava o produto antes de tudo, exige que ele tenha a padrão e
  limita a 10 ofertas não arquivadas por produto.
- `app_private.set_default_offer`: troca a oferta padrão. Trava o produto e
  depois as ofertas, em ordem de id. Recusa (P4093):
  - trocar uma padrão à venda por uma oferta que não está à venda: um
    rascunho nunca vira a padrão de um produto vendido;
  - até o #154, trocar a padrão de um produto publicado e à venda por uma de
    outro preço, moeda ou período. O webhook confere o valor pago contra a
    vitrine, e um checkout aberto pagaria um valor e esperaria outro.
  A troca é feita em duas instruções (tira a antiga, põe a nova), porque o
  índice parcial não é `deferrable`.
- `transition_offer_status`, reescrita pela definição viva (preserva o caminho
  do superadmin da 0032): trava o produto antes da oferta, e a padrão não é
  arquivada nem volta de `active` para `draft` enquanto o produto tiver outra
  oferta não arquivada (P4093).

As duas funções novas seguem o padrão de `transition_offer_status`: capability
e contexto assinado, `seller_owner` ativo ou superadmin do catálogo, `where`
por loja, produto e oferta, `search_path` fixo, idempotência no
`catalog_mutation_request` e auditoria.

**Quais lojas podem** criar a segunda oferta e trocar a padrão é decisão da
api (`APPSELL_MULTI_OFFER_SELLER_IDS`, vazia por padrão), não do banco.

Downgrade: só desce se nenhum produto tiver mais de uma oferta não arquivada.
Devolve `transition_offer_status`, derruba as duas funções, recria a unique e
o check de operações antigo. A api volta antes do downgrade.
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0068_segunda_oferta_do_produto"
down_revision = "0067_oferta_padrao_do_produto"
branch_labels = None
depends_on = None

MAX_OFERTAS_POR_PRODUTO = 10

_CRIA = (
    "app_private.create_product_offer("
    "uuid,uuid,uuid,uuid,text,text,bigint,text,text,uuid,text,uuid)"
)
_PADRAO = "app_private.set_default_offer(uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)"
_TRANSICAO = (
    "app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DAS_FUNCOES = "app_private.schema_ready_base_0016()"

_LOCK_TIMEOUT = "set local lock_timeout = '5s'"
_TIRA_UNIQUE = "alter table public.offer drop constraint offer_seller_product_key"
_VOLTA_UNIQUE = (
    "alter table public.offer add constraint offer_seller_product_key "
    "unique (seller_organization_id, catalog_product_id)"
)
_OPERACOES_ANTES = "('draft.update', 'product.publish', 'offer.status.update')"
_OPERACOES_DEPOIS = (
    "('draft.update', 'product.publish', 'offer.status.update', "
    "'offer.create', 'offer.default.update')"
)
_CHECK_OPERACOES = """
alter table app_private.catalog_mutation_request
  drop constraint catalog_mutation_request_operation_check,
  add constraint catalog_mutation_request_operation_check
    check (operation in OPERACOES)
"""

# A padrão não sai de cena enquanto houver outra oferta: primeiro troca-se a
# padrão, depois arquiva-se ou desativa-se a antiga.
_GUARDA_DA_PADRAO = """  if locked_offer.is_default and p_offer_status in ('archived', 'draft') and exists (
    select 1 from public.offer other
    where other.seller_organization_id = p_seller_organization_id
      and other.catalog_product_id = p_catalog_product_id
      and other.id <> p_offer_id
      and other.status <> 'archived'
  ) then
    raise exception 'default offer must be replaced before it leaves sale'
      using errcode = 'P4093';
  end if;
"""
# O produto é travado antes da oferta, na mesma ordem de create_product_offer,
# set_default_offer e update_product_course_offer_draft.
_TRAVA_DO_PRODUTO = """  perform 1 from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
"""
_ANCORA_DA_TRAVA = "  select * into locked_offer\n  from public.offer candidate\n"
_ANCORA_DA_GUARDA = "  if p_offer_status = 'active' and not exists (\n"
_TRANSICAO_TROCAS: tuple[tuple[str, str], ...] = (
    (_ANCORA_DA_TRAVA, _TRAVA_DO_PRODUTO + _ANCORA_DA_TRAVA),
    (_ANCORA_DA_GUARDA, _GUARDA_DA_PADRAO + _ANCORA_DA_GUARDA),
)

_DONO_OU_SUPERADMIN = """
  perform 1
  from public.seller_organization organization
  where organization.id = p_seller_organization_id
    and organization.status = 'active'
    and (
      exists (
        select 1 from public.seller_membership membership
        where membership.seller_organization_id = organization.id
          and membership.identity_user_id = p_identity_user_id
          and membership.role = 'seller_owner'
          and membership.status = 'active'
      )
      or app_private.platform_superadmin_catalog_allowed(
        p_identity_user_id, p_seller_organization_id, 'CAPABILITY'
      )
    )
  for key share of organization;
  if not found then
    raise exception 'offer owner is not authorized' using errcode = '28000';
  end if;
"""

_FUNCAO_CRIA = f"""
create function app_private.create_product_offer(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_offer_id uuid,
  p_title text,
  p_summary text,
  p_unit_amount_minor bigint,
  p_currency text,
  p_billing_period text,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table (offer_id uuid, offer_version bigint, created boolean)
language plpgsql security definer
set search_path = pg_catalog, app_private, public, pg_temp
as $$
declare
  existing_request app_private.catalog_mutation_request%rowtype;
  open_offers integer;
  response jsonb;
begin
  if app_private.context_text('capability') <> 'catalog.offer.create'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'offer create context is not authorized' using errcode = '28000';
  end if;
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_offer_id is null
     or p_title is null or btrim(p_title) <> p_title
     or octet_length(p_title) not between 2 and 120
     or p_summary is null or octet_length(p_summary) > 500
     or p_unit_amount_minor is null or p_unit_amount_minor < 0
     or p_unit_amount_minor > 99999999999
     or p_currency is null or length(p_currency) <> 3
     or translate(p_currency, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '') <> ''
     or p_billing_period is null
     or p_billing_period not in ('one_time', 'monthly', 'yearly')
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or length(p_body_sha256) <> 64
     or translate(p_body_sha256, '0123456789abcdef', '') <> '' then
    raise exception 'invalid offer create request' using errcode = '22023';
  end if;
{_DONO_OU_SUPERADMIN.replace("CAPABILITY", "catalog.offer.create")}
  perform pg_advisory_xact_lock(hashtextextended(
    'appsell.catalog.mutation:' || p_seller_organization_id::text ||
    '/offer.create/' || p_idempotency_key::text, 0));
  select * into existing_request
  from app_private.catalog_mutation_request request
  where request.seller_organization_id = p_seller_organization_id
    and request.operation = 'offer.create'
    and request.idempotency_key = p_idempotency_key;
  if found then
    if existing_request.identity_user_id = p_identity_user_id
       and existing_request.body_sha256 = p_body_sha256
       and existing_request.catalog_product_id = p_catalog_product_id
       and existing_request.offer_id = p_offer_id then
      return query select p_offer_id,
        (existing_request.result ->> 'offer_version')::bigint, false;
      return;
    end if;
    raise exception 'offer create idempotency conflict' using errcode = '23505';
  end if;

  perform 1 from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
    and product.status <> 'archived'
  for update;
  if not found then
    raise exception 'catalog product is unavailable' using errcode = '28000';
  end if;
  if not exists (
    select 1 from public.offer offer
    where offer.seller_organization_id = p_seller_organization_id
      and offer.catalog_product_id = p_catalog_product_id
      and offer.is_default
  ) then
    raise exception 'product has no default offer' using errcode = '55000';
  end if;
  select count(*) into open_offers
  from public.offer offer
  where offer.seller_organization_id = p_seller_organization_id
    and offer.catalog_product_id = p_catalog_product_id
    and offer.status <> 'archived';
  if open_offers >= {MAX_OFERTAS_POR_PRODUTO} then
    raise exception 'offer limit reached for product' using errcode = 'P4094';
  end if;

  insert into public.offer (
    seller_organization_id, id, catalog_product_id, title, summary, status,
    unit_amount_minor, currency, billing_period, is_default
  ) values (
    p_seller_organization_id, p_offer_id, p_catalog_product_id, p_title, p_summary,
    'draft', p_unit_amount_minor, p_currency, p_billing_period, false
  );

  response := jsonb_build_object('offer_id', p_offer_id, 'offer_version', 1);
  insert into app_private.catalog_mutation_request (
    seller_organization_id, operation, idempotency_key,
    identity_user_id, body_sha256, catalog_product_id, offer_id, result
  ) values (
    p_seller_organization_id, 'offer.create', p_idempotency_key,
    p_identity_user_id, p_body_sha256, p_catalog_product_id, p_offer_id, response
  );
  insert into public.audit_event (
    seller_organization_id, actor_identity_user_id, action, target_type,
    target_id, result, request_id, metadata
  ) values (
    p_seller_organization_id, p_identity_user_id, 'catalog.offer_created', 'offer',
    p_offer_id, 'succeeded', p_request_id,
    jsonb_build_object(
      'catalog_product_id', p_catalog_product_id,
      'unit_amount_minor', p_unit_amount_minor,
      'currency', p_currency,
      'billing_period', p_billing_period
    )
  );
  return query select p_offer_id, 1::bigint, true;
end
$$
"""  # noqa: S608 - só interpola constantes deste módulo

_FUNCAO_PADRAO = f"""
create function app_private.set_default_offer(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_offer_id uuid,
  p_expected_offer_version bigint,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table (offer_id uuid, previous_default_offer_id uuid, changed boolean)
language plpgsql security definer
set search_path = pg_catalog, app_private, public, pg_temp
as $$
declare
  existing_request app_private.catalog_mutation_request%rowtype;
  locked_product public.catalog_product%rowtype;
  target public.offer%rowtype;
  current_default public.offer%rowtype;
  response jsonb;
begin
  if app_private.context_text('capability') <> 'catalog.offer.default.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'default offer context is not authorized' using errcode = '28000';
  end if;
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_offer_id is null
     or p_expected_offer_version is null or p_expected_offer_version < 1
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or length(p_body_sha256) <> 64
     or translate(p_body_sha256, '0123456789abcdef', '') <> '' then
    raise exception 'invalid default offer request' using errcode = '22023';
  end if;
{_DONO_OU_SUPERADMIN.replace("CAPABILITY", "catalog.offer.default.update")}
  perform pg_advisory_xact_lock(hashtextextended(
    'appsell.catalog.mutation:' || p_seller_organization_id::text ||
    '/offer.default.update/' || p_idempotency_key::text, 0));
  select * into existing_request
  from app_private.catalog_mutation_request request
  where request.seller_organization_id = p_seller_organization_id
    and request.operation = 'offer.default.update'
    and request.idempotency_key = p_idempotency_key;
  if found then
    if existing_request.identity_user_id = p_identity_user_id
       and existing_request.body_sha256 = p_body_sha256
       and existing_request.catalog_product_id = p_catalog_product_id
       and existing_request.offer_id = p_offer_id then
      return query select p_offer_id,
        (existing_request.result ->> 'previous_default_offer_id')::uuid, false;
      return;
    end if;
    raise exception 'default offer idempotency conflict' using errcode = '23505';
  end if;

  -- Produto primeiro, depois as ofertas em ordem de id: a mesma ordem de toda
  -- função que escreve em offer, para duas trocas concorrentes não se travarem.
  select * into locked_product from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
  if not found or locked_product.status = 'archived' then
    raise exception 'catalog product is unavailable' using errcode = '28000';
  end if;
  perform 1 from public.offer offer
  where offer.seller_organization_id = p_seller_organization_id
    and offer.catalog_product_id = p_catalog_product_id
  order by offer.id
  for update;

  select * into target from public.offer offer
  where offer.seller_organization_id = p_seller_organization_id
    and offer.catalog_product_id = p_catalog_product_id
    and offer.id = p_offer_id;
  if not found then
    raise exception 'catalog offer is unavailable' using errcode = '28000';
  end if;
  if target.version <> p_expected_offer_version then
    raise exception 'catalog offer version conflict' using errcode = '40001';
  end if;
  if target.status = 'archived' then
    raise exception 'archived offer cannot be the default' using errcode = 'P4093';
  end if;
  select * into current_default from public.offer offer
  where offer.seller_organization_id = p_seller_organization_id
    and offer.catalog_product_id = p_catalog_product_id
    and offer.is_default;
  if not found then
    raise exception 'product has no default offer' using errcode = '55000';
  end if;

  if current_default.id <> target.id then
    if current_default.status = 'active' and target.status <> 'active' then
      raise exception 'default offer on sale can only be replaced by an offer on sale'
        using errcode = 'P4093';
    end if;
    if locked_product.status = 'published' and current_default.status = 'active'
       and (target.unit_amount_minor, target.currency, target.billing_period)
           is distinct from
           (current_default.unit_amount_minor, current_default.currency,
            current_default.billing_period) then
      raise exception 'default offer price change requires pinned orders'
        using errcode = 'P4093';
    end if;
    update public.offer
    set is_default = false, version = version + 1, updated_at = clock_timestamp()
    where seller_organization_id = p_seller_organization_id and id = current_default.id;
    update public.offer
    set is_default = true, version = version + 1, updated_at = clock_timestamp()
    where seller_organization_id = p_seller_organization_id and id = target.id;
  end if;

  response := jsonb_build_object(
    'offer_id', p_offer_id, 'previous_default_offer_id', current_default.id
  );
  insert into app_private.catalog_mutation_request (
    seller_organization_id, operation, idempotency_key,
    identity_user_id, body_sha256, catalog_product_id, offer_id, result
  ) values (
    p_seller_organization_id, 'offer.default.update', p_idempotency_key,
    p_identity_user_id, p_body_sha256, p_catalog_product_id, p_offer_id, response
  );
  insert into public.audit_event (
    seller_organization_id, actor_identity_user_id, action, target_type,
    target_id, result, request_id, metadata
  ) values (
    p_seller_organization_id, p_identity_user_id, 'catalog.offer_default_changed',
    'offer', p_offer_id, 'succeeded', p_request_id,
    jsonb_build_object(
      'catalog_product_id', p_catalog_product_id,
      'previous_default_offer_id', current_default.id
    )
  );
  return query select p_offer_id, current_default.id, current_default.id <> target.id;
end
$$
"""  # noqa: S608 - só interpola constantes deste módulo

_GUARDA_DA_DESCIDA = """
do $migration$
begin
  -- Qualquer produto com mais de uma oferta (inclusive arquivada) impede a
  -- unique antiga; um pedido das operações novas impede o check antigo.
  if exists (
    select 1 from public.offer
    group by seller_organization_id, catalog_product_id
    having count(*) > 1
  ) or exists (
    select 1 from app_private.catalog_mutation_request
    where operation in ('offer.create', 'offer.default.update')
  ) then
    raise exception 'offers created by the 0068 block its downgrade'
      using errcode = '55000';
  end if;
end $migration$
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


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _ready_versoes() -> tuple[str, str]:
    anterior = _carregar(down_revision)
    _, para_anterior = anterior._ready_versoes(anterior._migration_anterior())
    return para_anterior, para_anterior[:-2] + f",'{revision}'))"


def _ready_funcoes() -> tuple[str, str]:
    """Âncora: as funções que a 0064 pôs no readiness. Acrescenta as duas novas."""
    de = _carregar("0064_vendas_ativadas_pela_loja")._ready_funcao()[1]
    linha = "            and to_regprocedure('{}') is not null\n"
    novas = linha.format(_CRIA) + linha.format(_PADRAO)
    return de, de + novas


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para)


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_LOCK_TIMEOUT)
    op.execute(_TIRA_UNIQUE)
    op.execute(_CHECK_OPERACOES.replace("OPERACOES", _OPERACOES_DEPOIS))
    for de, para in _TRANSICAO_TROCAS:
        op.execute(_reescreve(_TRANSICAO, de, para))
    op.execute(_FUNCAO_CRIA)
    op.execute(_FUNCAO_PADRAO)
    for funcao in (_CRIA, _PADRAO):
        op.execute(f"revoke all on function {funcao} from public")
        op.execute(f"grant execute on function {funcao} to appsell_runtime")
    de, para = _ready_versoes()
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, de, para))
    de, para = _ready_funcoes()
    op.execute(_reescreve(_BASE_DAS_FUNCOES, de, para))


def downgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_LOCK_TIMEOUT)
    op.execute(_GUARDA_DA_DESCIDA)
    de, para = _ready_funcoes()
    op.execute(_reescreve(_BASE_DAS_FUNCOES, para, de))
    de, para = _ready_versoes()
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, para, de))
    op.execute(f"drop function {_PADRAO}")
    op.execute(f"drop function {_CRIA}")
    for de, para in reversed(_TRANSICAO_TROCAS):
        op.execute(_reescreve(_TRANSICAO, para, de))
    op.execute(_CHECK_OPERACOES.replace("OPERACOES", _OPERACOES_ANTES))
    op.execute(_VOLTA_UNIQUE)
