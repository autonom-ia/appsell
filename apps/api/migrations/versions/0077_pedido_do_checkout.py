"""Pedido do checkout guardado no servidor (#154, fatia 1 da opção A).

Hoje o Sell não guarda nada da venda antes do webhook: o `create-session` lê o
preço da vitrine, cria o intent no Financial e esquece. Esta migration cria o
pedido, gravado antes do Financial, com a oferta e os valores daquele momento.
Nada é recalculado depois.

Tabela de loja, só acessível por função `security definer`:

* `app_private.checkout_order`: um pedido por tentativa de compra. Guarda a
  oferta padrão (`offer_id` e `offer_version`), os valores calculados pelo web
  (`unit_amount_minor`, `discount_minor`, `charged_minor`, `platform_fee_minor`,
  `seller_net_minor`, `plan_key`), o comprador só em hash (`buyer_sha256`) e,
  quando o Financial responde, `purchase_sha256` = sha256 do id do intent, a
  mesma chave da atribuição (0075) e do webhook. Os nomes foram combinados com
  a Dinheiro, porque a conciliação (#147) vai lê-los.

  Os checks são só de consistência (`charged = unit - discount`,
  `net = charged - fee`). O preço mínimo continua só em `lib/minimum-price.ts`
  (#546): o banco não ganha uma segunda regra de mínimo.

  Sem FK para a oferta nem para a loja: só as funções abaixo gravam, lendo a
  oferta sob trava, e assim os caminhos que apagam catálogo (reset da demo,
  exclusão de loja) não mudam.

Funções, ambas do worker do checkout (capacidade `commerce.checkout.order`,
sem loja no envelope, como o destino do repasse da 0024/0073: a loja é a chave
de busca vinda da vitrine, não um claim):

* `create_checkout_order`: trava a oferta padrão do produto publicado da loja
  ativa e confere que ela está à venda com o preço, a moeda e o período que o
  web calculou e com a publicação que a vitrine mostrou. Divergiu: P4095 (a api
  responde 409 `price_changed`). Não faz conta de preço.
* `attach_checkout_intent`: liga o pedido aberto ao hash do intent, uma vez só.
  O mesmo hash de novo é idempotente; outro hash devolve `false`.

Quem chama: a rota interna da api, chamada pelo `create-session` só para as
lojas do piloto `APPSELL_PINNED_ORDER_SELLER_IDS` (vazia por padrão). O
ingress ainda não lê o pedido (fatia 3).

A descida recusa enquanto existir pedido gravado: ele é registro de venda.
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0077_pedido_do_checkout"
down_revision = "0074_acesso_em_disputa"
branch_labels = None
depends_on = None

CAPACIDADE = "commerce.checkout.order"
# A oferta não é mais a que a vitrine mostrou: preço, moeda, período,
# publicação, padrão ou status mudaram, ou a loja saiu do ar.
PRECO_MUDOU = "P4095"
# O mesmo TTL da sessão do checkout (`CHECKOUT_SESSION_TTL_SECONDS` do
# create-session): o pedido vale enquanto a sessão de pagamento vale.
VALIDADE_EM_SEGUNDOS = 31 * 60
PLANOS = ("launch", "growth", "scale")
SEARCH_PATH = "set search_path=pg_catalog,app_private,public,pg_temp"

ASSINATURAS = (
    "app_private.create_checkout_order(uuid,uuid,uuid,bigint,text,text,bigint,bigint,text,text)",
    "app_private.attach_checkout_intent(uuid,uuid,text)",
)


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}_para_0077", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A lista do readiness vem da anterior na cadeia (`_READY_ATE_<n>` dela).
READY_ANTES: str = getattr(_carregar(down_revision), f"_READY_ATE_{down_revision.split('_', 1)[0]}")
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0077 = READY_DEPOIS
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
# A última migration que acrescentou função ao readiness. As linhas desta
# entram depois das dela, e a próxima lê `_ready_funcoes()` desta.
_ULTIMA_COM_FUNCAO_NO_READY = down_revision

_READY_DO_PEDIDO = (
    "            and to_regclass('app_private.checkout_order') is not null\n"
    "            and (select count(*) from pg_catalog.pg_class tabela\n"
    "              where tabela.oid = to_regclass('app_private.checkout_order')\n"
    "                and tabela.relrowsecurity and tabela.relforcerowsecurity) = 1\n"
    "            and to_regprocedure('app_private.create_checkout_order("
    "uuid,uuid,uuid,bigint,text,text,bigint,bigint,text,text)') is not null\n"
    "            and to_regprocedure('app_private.attach_checkout_intent("
    "uuid,uuid,text)') is not null\n"
)


def _ready_funcao() -> tuple[str, str]:
    de: str = _carregar(_ULTIMA_COM_FUNCAO_NO_READY)._ready_funcoes()[1]
    return de, de + _READY_DO_PEDIDO


# Nome que a próxima migration com função no readiness lê para achar o fim
# deste bloco: (antes, depois desta).
def _ready_funcoes() -> tuple[str, str]:
    return _ready_funcao()


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


def _check_sha(coluna: str) -> str:
    return f"char_length({coluna}) = 64 and translate({coluna},'0123456789abcdef','') = ''"


_LISTA_DE_PLANOS = ",".join(f"'{plano}'" for plano in PLANOS)

_CRIA_TABELA = f"""
    create table app_private.checkout_order (
      seller_organization_id uuid not null,
      id uuid not null default gen_random_uuid(),
      purchase_sha256 text check ({_check_sha("purchase_sha256")}),
      product_id uuid not null,
      offer_id uuid not null,
      offer_version bigint not null check (offer_version > 0),
      coupon_id uuid,
      currency text not null check (currency = 'BRL'),
      billing_period text not null check (billing_period = 'one_time'),
      unit_amount_minor bigint not null check (unit_amount_minor > 0),
      discount_minor bigint not null default 0 check (discount_minor >= 0),
      charged_minor bigint not null check (charged_minor > 0),
      platform_fee_minor bigint check (platform_fee_minor >= 0),
      seller_net_minor bigint check (seller_net_minor >= 0),
      plan_key text not null check (plan_key in ({_LISTA_DE_PLANOS})),
      buyer_sha256 text not null check ({_check_sha("buyer_sha256")}),
      status text not null default 'open'
        check (status in ('open','intent_created','paid','amount_mismatch','expired')),
      created_at timestamptz not null default clock_timestamp(),
      expires_at timestamptz not null,
      constraint checkout_order_pkey primary key (seller_organization_id,id),
      constraint checkout_order_purchase_key unique (seller_organization_id,purchase_sha256),
      constraint checkout_order_charged_check
        check (charged_minor = unit_amount_minor - discount_minor),
      constraint checkout_order_fee_pair_check
        check ((platform_fee_minor is null) = (seller_net_minor is null)),
      constraint checkout_order_net_check
        check (platform_fee_minor is null
          or seller_net_minor = charged_minor - platform_fee_minor),
      constraint checkout_order_intent_check
        check ((status <> 'open' or purchase_sha256 is null)
          and (status not in ('intent_created','paid','amount_mismatch')
            or purchase_sha256 is not null)),
      constraint checkout_order_expiry_check check (expires_at > created_at)
    )
"""

# Só o worker do checkout, sem loja no envelope (mesma trava da 0024/0073).
_GUARDA = f"""
  if app_private.context_text('capability') is distinct from '{CAPACIDADE}'
     or app_private.context_uuid('seller_organization_id') is not null
     or p_seller_organization_id is null then
    raise exception 'checkout order context is not authorized' using errcode='42501';
  end if;
"""

_CRIA_PEDIDO = f"""
create function app_private.create_checkout_order(
  p_seller_organization_id uuid,p_product_id uuid,p_publication_id uuid,
  p_unit_amount_minor bigint,p_currency text,p_billing_period text,
  p_platform_fee_minor bigint,p_seller_net_minor bigint,p_plan_key text,p_buyer_sha256 text
) returns table(order_id uuid,offer_id uuid,offer_version bigint,charged_minor bigint,
  expires_at timestamptz)
language plpgsql volatile security definer
{SEARCH_PATH} as $$
#variable_conflict use_column
declare v_offer_id uuid; v_offer_version bigint; v_order_id uuid; v_expires_at timestamptz;
begin
{_GUARDA}
  -- A oferta padrão à venda, travada até o fim da transação: uma troca de
  -- preço concorrente espera o pedido ser gravado, ou o pedido vê o preço novo.
  select offer.id,offer.version into v_offer_id,v_offer_version
  from public.seller_organization seller
  join public.catalog_product product
    on product.seller_organization_id=seller.id
   and product.id=p_product_id
   and product.status='published'
   and product.published_snapshot_id=p_publication_id
  join public.offer offer
    on offer.seller_organization_id=product.seller_organization_id
   and offer.catalog_product_id=product.id
   and offer.is_default
   and offer.status='active'
  where seller.id=p_seller_organization_id and seller.status='active'
  for share of offer;
  if v_offer_id is null then
    raise exception 'checkout offer changed' using errcode='{PRECO_MUDOU}';
  end if;
  -- Relida depois da trava: o valor que vale é o da oferta travada.
  if exists (select 1 from public.offer offer
             where offer.seller_organization_id=p_seller_organization_id
               and offer.id=v_offer_id
               and (offer.unit_amount_minor is distinct from p_unit_amount_minor
                 or rtrim(offer.currency) is distinct from p_currency
                 or offer.billing_period is distinct from p_billing_period)) then
    raise exception 'checkout offer changed' using errcode='{PRECO_MUDOU}';
  end if;
  insert into app_private.checkout_order(
    seller_organization_id,product_id,offer_id,offer_version,currency,billing_period,
    unit_amount_minor,discount_minor,charged_minor,platform_fee_minor,seller_net_minor,
    plan_key,buyer_sha256,expires_at)
  values (p_seller_organization_id,p_product_id,v_offer_id,v_offer_version,p_currency,
    p_billing_period,p_unit_amount_minor,0,p_unit_amount_minor,p_platform_fee_minor,
    p_seller_net_minor,p_plan_key,p_buyer_sha256,
    clock_timestamp() + interval '{VALIDADE_EM_SEGUNDOS} seconds')
  returning checkout_order.id,checkout_order.expires_at into v_order_id,v_expires_at;
  return query select v_order_id,v_offer_id,v_offer_version,p_unit_amount_minor,v_expires_at;
end $$
"""  # noqa: S608 - só constantes deste módulo entram no texto

_LIGA_INTENT = f"""
create function app_private.attach_checkout_intent(
  p_seller_organization_id uuid,p_order_id uuid,p_purchase_sha256 text
) returns boolean
language plpgsql volatile security definer
{SEARCH_PATH} as $$
#variable_conflict use_column
declare v_atual text; v_status text;
begin
{_GUARDA}
  if p_order_id is null or p_purchase_sha256 is null then
    raise exception 'checkout order intent is required' using errcode='22023';
  end if;
  select pedido.purchase_sha256,pedido.status into v_atual,v_status
  from app_private.checkout_order pedido
  where pedido.seller_organization_id=p_seller_organization_id and pedido.id=p_order_id
  for update;
  if v_status is null then
    return false;
  end if;
  if v_status <> 'open' then
    return coalesce(v_atual = p_purchase_sha256, false);
  end if;
  update app_private.checkout_order pedido
  set purchase_sha256=p_purchase_sha256,status='intent_created'
  where pedido.seller_organization_id=p_seller_organization_id and pedido.id=p_order_id;
  return true;
end $$
"""  # noqa: S608 - só constantes deste módulo entram no texto

FUNCOES = (_CRIA_PEDIDO, _LIGA_INTENT)

_RECUSA_DESCIDA = """
do $migration$
begin
  if exists (select 1 from app_private.checkout_order) then
    raise exception 'checkout orders were recorded; they are sale records'
      using errcode='55000';
  end if;
end $migration$
"""


def upgrade() -> None:
    nome = "app_private.checkout_order"
    op.execute(_CRIA_TABELA)
    op.execute(f"alter table {nome} owner to appsell_owner")
    op.execute(f"alter table {nome} enable row level security")
    op.execute(f"alter table {nome} force row level security")
    op.execute(
        f"create policy checkout_order_owner_all on {nome} "
        "to appsell_owner using (true) with check (true)"
    )
    op.execute(f"revoke all on table {nome} from public,appsell_runtime")
    for sql in FUNCOES:
        op.execute(sql)
    for assinatura in ASSINATURAS:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, READY_DEPOIS))
    de, para = _ready_funcao()
    op.execute(_reescreve(_BASE_DO_ACESSO, de, para))


def downgrade() -> None:
    op.execute(_RECUSA_DESCIDA)
    de, para = _ready_funcao()
    op.execute(_reescreve(_BASE_DO_ACESSO, para, de))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    for assinatura in reversed(ASSINATURAS):
        op.execute(f"drop function {assinatura}")
    op.execute("drop table app_private.checkout_order")
