"""Uma checagem única que diz se o aluno tem acesso e, se não tem, por quê (#558).

Hoje cada função de conteúdo (`authorize_*`, `read_learning_player`,
`record_lesson_progress`, `read_buyer_library`) repete o mesmo predicado e nega
com o mesmo `28000`. O aluno vê "Não foi possível abrir esta aula" para um
estorno, uma loja suspensa ou um acesso vencido, e ninguém sabe dizer qual.

Esta migration cria `app_private.buyer_access_decision(comprador, entitlement)`,
que devolve `(allowed, reason, update_pending)`:

* `not_found`: o entitlement não existe **ou é de outro comprador**. Os dois
  casos respondem igual de propósito: a função não pode virar oráculo de ids.
* `refunded`: revogado, com compra estornada e nenhuma compra ativa do mesmo
  produto para esta pessoa. Derivado de `app_private.entitlement_grant` por loja,
  comprador e produto; nada no fulfillment muda (trilha Dinheiro). Limite
  conhecido: um acesso revogado à mão de quem tem uma compra antiga estornada do
  mesmo produto aparece como `refunded`. Só o próprio comprador vê o motivo, e a
  derivação sai quando o motivo da revogação for gravado.
* `revoked`, `expired`, `not_started`, `seller_unavailable`,
  `product_unavailable`.
* `active`, com `update_pending=true` quando a loja publicou uma versão que não
  chegou a este aluno (a versão nova removeu algo, #420).

Nega por padrão: status desconhecido vira `unavailable`, e a função só devolve
`allowed=true` no fim do caminho feliz. `disputed` e `subscription_lapsed` são
códigos reservados; esta versão não os emite.

A função **não libera nada**: as `authorize_*` continuam sendo o portão. Ela só
explica uma negação, e o teste de Postgres prova que nega exatamente quando a
biblioteca e o asset negam.

Contexto: o mesmo da biblioteca (`library.read`, sem loja no envelope).

Downgrade: tira a função e devolve o `schema_ready` ao estado da anterior na
cadeia.
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0069_motivo_do_acesso"
down_revision = "0072_relatorio_de_vendas"
branch_labels = None
depends_on = None

_FUNCAO = "app_private.buyer_access_decision(uuid,uuid)"

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
# A última migration que acrescentou função ao readiness. A decisão entra
# depois da linha dela.
_ULTIMA_COM_FUNCAO_NO_READY = "0068_segunda_oferta_do_produto"

_READY_DA_DECISAO = f"            and to_regprocedure('{_FUNCAO}') is not null\n"

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

# Congelada aqui: a migration precisa produzir sempre o mesmo SQL.
_DECISAO = r"""
create function app_private.buyer_access_decision(
  p_identity_user_id uuid, p_entitlement_id uuid
) returns table(allowed boolean, reason text, update_pending boolean)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  v_status text; v_starts timestamptz; v_expires timestamptz;
  v_publication uuid; v_seller uuid; v_product uuid;
  v_seller_status text; v_product_status text; v_current_publication uuid;
begin
  if not app_private.library_context_allowed(p_identity_user_id,'library.read') then
    raise exception 'library context is not authorized' using errcode='28000';
  end if;

  select entitlement.status, entitlement.starts_at, entitlement.expires_at,
         entitlement.publication_id, entitlement.seller_organization_id,
         entitlement.catalog_product_id, organization.status, product.status,
         product.published_snapshot_id
    into v_status, v_starts, v_expires, v_publication, v_seller, v_product,
         v_seller_status, v_product_status, v_current_publication
    from buyer_entitlement entitlement
    join seller_organization organization
      on organization.id=entitlement.seller_organization_id
    join catalog_product product
      on product.seller_organization_id=entitlement.seller_organization_id
     and product.id=entitlement.catalog_product_id
   where entitlement.id=p_entitlement_id
     and entitlement.buyer_identity_user_id=p_identity_user_id;

  -- Inexistente e de outro comprador respondem igual.
  if not found then
    return query select false, 'not_found'::text, false;
    return;
  end if;

  if v_status='revoked' then
    if exists(
         select 1 from app_private.entitlement_grant grant_row
          where grant_row.seller_organization_id=v_seller
            and grant_row.buyer_identity_user_id=p_identity_user_id
            and grant_row.catalog_product_id=v_product
            and grant_row.status='refunded')
       and not exists(
         select 1 from app_private.entitlement_grant grant_row
          where grant_row.seller_organization_id=v_seller
            and grant_row.buyer_identity_user_id=p_identity_user_id
            and grant_row.catalog_product_id=v_product
            and grant_row.status='active') then
      return query select false, 'refunded'::text, false;
      return;
    end if;
    return query select false, 'revoked'::text, false;
    return;
  end if;

  if v_status='expired'
     or (v_status='active' and v_expires is not null and v_expires<=clock_timestamp()) then
    return query select false, 'expired'::text, false;
    return;
  end if;

  -- Status que esta função não conhece nunca vira acesso.
  if v_status is distinct from 'active' then
    return query select false, 'unavailable'::text, false;
    return;
  end if;

  if v_starts is null or v_starts>clock_timestamp() then
    return query select false, 'not_started'::text, false;
    return;
  end if;

  if v_seller_status is distinct from 'active' then
    return query select false, 'seller_unavailable'::text, false;
    return;
  end if;

  if v_product_status is distinct from 'published' then
    return query select false, 'product_unavailable'::text, false;
    return;
  end if;

  return query select true, 'active'::text,
    coalesce(v_current_publication is not null
      and v_current_publication is distinct from v_publication, false);
end $$
"""


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A lista do readiness vem da anterior na cadeia (`_READY_ATE_<n>` dela), não de
# uma cópia escrita aqui: assim ela não perde as versões que entraram antes.
READY_ANTES: str = getattr(_carregar(down_revision), f"_READY_ATE_{down_revision.split('_', 1)[0]}")
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr`.
_READY_ATE_0069 = READY_DEPOIS


def _ready_funcao() -> tuple[str, str]:
    de: str = _carregar(_ULTIMA_COM_FUNCAO_NO_READY)._ready_funcoes()[1]
    return de, de + _READY_DA_DECISAO


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_DECISAO)
    op.execute(f"revoke all on function {_FUNCAO} from public")
    op.execute(f"grant execute on function {_FUNCAO} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", READY_ANTES)
            .replace("PARA", READY_DEPOIS)
        )
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", de).replace("PARA", para))


def downgrade() -> None:
    op.execute("set local role appsell_owner")
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", para).replace("PARA", de))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", READY_DEPOIS)
            .replace("PARA", READY_ANTES)
        )
    op.execute(f"drop function {_FUNCAO}")
