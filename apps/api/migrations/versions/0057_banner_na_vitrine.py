"""O banner do produto aparece na vitrine pública (#387).

Separada da 0056 de propósito. O parser da vitrine no navegador recusa chave
que não conhece — foi assim que a 0052 exigiu a ordem web antes de banco. Se o
banner aparecesse na projeção antes de o web novo estar no ar, toda loja com
produto publicado responderia erro.

Então a 0056 prepara o banco e as funções, o web e a API sobem, e só depois
esta migration acende o campo. A partir daqui a página de vendas encontra o
banner que o vendedor enviou.

Revision ID: 0057_banner_na_vitrine
Revises: 0056_banner_do_produto
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0057_banner_na_vitrine"
down_revision = "0056_banner_do_produto"
branch_labels = None
depends_on = None


def _modulo(nome: str, apelido: str) -> ModuleType:
    caminho = Path(__file__).with_name(nome)
    spec = importlib_util.spec_from_file_location(apelido, caminho)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não consegui carregar {nome}")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# O bloco entra logo depois do da capa, antes do resumo do conteúdo.
_ANCORA = "    || app_private.published_curriculum_summary(publication.snapshot)"
_BLOCO = """    || case when product.banner_object_key is null then '{}'::jsonb
         else jsonb_build_object('banner',jsonb_build_object(
           'object_key',product.banner_object_key,
           'storage_version_id',product.banner_storage_version_id,
           'media_type',product.banner_media_type,
           'width',product.banner_width,'height',product.banner_height)) end
"""

_READY = "app_private.schema_ready()"
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_READY_ATE_0056 = "'0054_banner_da_loja','0055_seller_financial_binding','0056_banner_do_produto'))"
_READY_ATE_0057 = (
    "'0054_banner_da_loja','0055_seller_financial_binding','0056_banner_do_produto',"
    "'0057_banner_na_vitrine'))"
)

_REESCREVE = """
do $migration$
declare
  definicao text;
  reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao) - length(replace(definicao, $de$DE$de$, ''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO não continha a âncora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  execute reescrita;
end $migration$
"""


def _vitrine(com_banner_do_produto: bool) -> str:
    """A vitrine como a 0055 a deixou, com ou sem o banner do produto.

    O ponto de partida é a 0055 e não a 0054: ela acrescentou o vínculo com o
    Financial à mesma função, e recriar a partir da 0054 apagaria esse bloco.
    """
    m55 = _modulo("0055_seller_financial_binding.py", "appsell_migration_0055")
    texto = m55._storefront_0054()
    if texto.count(m55._SELLER_ANCHOR) != 1:
        raise RuntimeError("a âncora do vínculo com o Financial não está onde a 0055 a deixou")
    texto = texto.replace(m55._SELLER_ANCHOR, m55._SELLER_WITH_FINANCIAL, 1)
    if not com_banner_do_produto:
        return texto
    if texto.count(_ANCORA) != 1:
        raise RuntimeError("a âncora da vitrine não aparece exatamente uma vez")
    return texto.replace(_ANCORA, _BLOCO + _ANCORA, 1)


def upgrade() -> None:
    op.execute(_vitrine(com_banner_do_produto=True))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0056)
            .replace("PARA", _READY_ATE_0057)
        )


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0057)
            .replace("PARA", _READY_ATE_0056)
        )
    op.execute(_vitrine(com_banner_do_produto=False))
