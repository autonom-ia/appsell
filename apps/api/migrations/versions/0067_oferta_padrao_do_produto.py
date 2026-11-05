"""Toda oferta ganha a marca de padrão do produto: fase 1 de várias ofertas (#553).

Hoje um produto tem uma oferta só, garantida pela unique
`offer_seller_product_key (seller_organization_id, catalog_product_id)`.
Para várias ofertas, cada produto passa a ter **uma oferta padrão**, e é ela
que a vitrine mostra e o checkout cobra.

Esta fase é só expand e não muda nada que alguém veja:

- `offer.is_default boolean not null default true`. Toda oferta que existe é a
  única do seu produto, então é a padrão. O default `true` mantém o insert
  legado de `update_product_course_offer_draft` e os seeds criando a padrão
  sem precisar mudar. Só a função da fase 2 vai criar oferta com `false`.
- Índice único parcial `offer_one_default_per_product`: no máximo uma padrão
  por produto.
- A vitrine (`read_public_storefront`, última definição na 0052) junta só a
  oferta padrão. Com uma oferta por produto, a saída é idêntica.
- O acesso gratuito (`grant_public_product_access`, 0031) escolhe primeiro
  uma oferta ativa, depois a padrão. Produto com qualquer oferta à venda
  continua exigindo checkout (P4222).
- Publicar exige a oferta padrão: `publish_product` (0008, com o patch da
  0016) e `publish_product_with_access` (0034) recusam com 55000, o mesmo
  erro de oferta não publicável, quando o `p_offer_id` não é a padrão.
- `create_product_access_link` (0034) não muda: já recusa quando qualquer
  oferta do produto está ativa, que é a escolha conservadora.
- **A unique antiga continua.** A segunda oferta só passa a ser possível na
  fase 2 (0068), que a derruba e cria as funções de criar oferta e trocar a
  padrão.

As duas funções são reescritas por âncora (`pg_get_functiondef` + `replace`,
exigindo a âncora exatamente uma vez), no padrão da 0064 e da 0065.

Downgrade: devolve as duas funções ao texto anterior, derruba o índice e a
coluna e tira a versão do readiness. Nenhuma oferta é apagada.
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0067_oferta_padrao_do_produto"
down_revision = "0066_consentimento_de_email"
branch_labels = None
depends_on = None

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_VITRINE = "app_private.read_public_storefront(text)"
_ACESSO = "app_private.grant_public_product_access(uuid,uuid,uuid,uuid,text,uuid)"
_PUBLICA = "app_private.publish_product(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid)"
_PUBLICA_COM_ACESSO = "app_private.publish_product_with_access(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,text,uuid,text,uuid)"
_RECUSA_NAO_PADRAO = "raise exception 'catalog offer is not the default' using errcode='55000'"

# Âncora da 0052: o join da vitrine com a oferta do produto.
_VITRINE_TROCA = (
    "   and offer.catalog_product_id=product.id\n",
    "   and offer.catalog_product_id=product.id\n   and offer.is_default\n",
)
# Âncora da 0031: qual oferta decide se o produto é gratuito.
_ACESSO_TROCA = (
    "  order by offer.version desc,offer.id desc\n",
    "  order by (offer.status='active') desc,offer.is_default desc,"
    "offer.version desc,offer.id desc\n",
)

# Âncora da 0008: publicar só a oferta padrão. A 0016 não toca nesta linha.
_PUBLICA_TROCA = (
    "raise exception 'catalog offer is not publishable' using errcode='55000'; end if;\n",
    "raise exception 'catalog offer is not publishable' using errcode='55000'; end if;\n"
    f"          if not locked_offer.is_default then\n            {_RECUSA_NAO_PADRAO}; end if;\n",
)
# Âncora da 0034: a venda ativa só a oferta padrão (defesa em profundidade; a
# publish_product chamada antes já recusa).
_PUBLICA_COM_ACESSO_TROCA = (
    "    if locked_offer.unit_amount_minor <= 0 then\n",
    f"    if not locked_offer.is_default then\n      {_RECUSA_NAO_PADRAO};\n    end if;\n"
    "    if locked_offer.unit_amount_minor <= 0 then\n",
)

_LOCK_TIMEOUT = "set local lock_timeout = '5s'"
_COLUNA = "alter table public.offer add column is_default boolean not null default true"
_INDICE = """
create unique index offer_one_default_per_product
  on public.offer (seller_organization_id, catalog_product_id)
  where is_default
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


def _migration_anterior() -> ModuleType:
    """Lê a lista de versões do readiness que a migration anterior deixou."""
    caminho = Path(__file__).with_name(f"{down_revision}.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_anterior_0067", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError("a migration anterior nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _ready_versoes(anterior: ModuleType) -> tuple[str, str]:
    numero = down_revision.split("_", 1)[0]
    de: str = getattr(anterior, f"_READY_ATE_{numero}")
    return de, de[:-2] + f",'{revision}'))"


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para)


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    # `add column` e a reescrita das funções pedem lock exclusivo em `offer`.
    # Com o checkout no ar, esperar mais que isso trava a vitrine; melhor falhar
    # e rodar de novo.
    op.execute(_LOCK_TIMEOUT)
    op.execute(_COLUNA)
    op.execute(_INDICE)
    op.execute(_reescreve(_VITRINE, *_VITRINE_TROCA))
    op.execute(_reescreve(_ACESSO, *_ACESSO_TROCA))
    op.execute(_reescreve(_PUBLICA, *_PUBLICA_TROCA))
    op.execute(_reescreve(_PUBLICA_COM_ACESSO, *_PUBLICA_COM_ACESSO_TROCA))
    de, para = _ready_versoes(_migration_anterior())
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, de, para))


def downgrade() -> None:
    op.execute("set local role appsell_owner")
    de, para = _ready_versoes(_migration_anterior())
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, para, de))
    op.execute(_reescreve(_PUBLICA_COM_ACESSO, *reversed(_PUBLICA_COM_ACESSO_TROCA)))
    op.execute(_reescreve(_PUBLICA, *reversed(_PUBLICA_TROCA)))
    op.execute(_reescreve(_ACESSO, _ACESSO_TROCA[1], _ACESSO_TROCA[0]))
    op.execute(_reescreve(_VITRINE, _VITRINE_TROCA[1], _VITRINE_TROCA[0]))
    op.execute("drop index public.offer_one_default_per_product")
    op.execute("alter table public.offer drop column is_default")
