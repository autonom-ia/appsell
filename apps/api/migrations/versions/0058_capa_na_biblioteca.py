"""A capa do produto aparece na biblioteca do aluno (#387).

A área do aluno mostrava só texto: nome da loja, título, descrição e
progresso. O produto tem capa desde a 0037, e ela já aparece no cartão da
loja, na lista do painel e na página de vendas — faltava aqui, que é onde o
comprador volta toda vez que retoma o curso.

A capa vem da tabela, não do conteúdo publicado: o snapshot guarda o que foi
publicado do produto, e a imagem é atualizável sem republicar. O bloco é
condicional, como na vitrine: produto sem capa não ganha a chave, e o cartão
desenha a arte gerada a partir do nome.

Ordem: o web precisa subir ANTES desta migration. O parser do navegador
confere as chaves exatas do item, e uma chave nova derruba a biblioteca de
quem ainda estiver com a versão antiga.

Revision ID: 0058_capa_na_biblioteca
Revises: 0057_banner_na_vitrine
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0058_capa_na_biblioteca"
down_revision = "0057_banner_na_vitrine"
branch_labels = None
depends_on = None


_INICIO = "        create or replace function app_private.read_buyer_library"
_FIM = '        """'


def _texto_da_0007() -> str:
    """O corpo da função como a 0007 o escreveu.

    A 0007 guarda os comandos numa lista dentro de `upgrade()`, então não dá
    para importar: o texto é recortado do arquivo, com as asserções abaixo
    para falhar alto se ele sair do lugar.
    """
    fonte = Path(__file__).with_name("0007_learning_progress.py").read_text(encoding="utf-8")
    if fonte.count(_INICIO) != 1:
        raise RuntimeError("a função da biblioteca não está onde a 0007 a deixou")
    inicio = fonte.index(_INICIO)
    fim = fonte.index(_FIM, inicio)
    texto = fonte[inicio:fim]
    if "read_buyer_library" not in texto or "buyer_entitlement" not in texto:
        raise RuntimeError("o recorte da 0007 não parece a função da biblioteca")
    return texto


_ANCORA = """            'product',(publication.snapshot->'product') || jsonb_build_object(
              'publication_id',publication.id,'publication_version',publication.version
            ),"""

_COM_CAPA = """            'product',(publication.snapshot->'product') || jsonb_build_object(
              'publication_id',publication.id,'publication_version',publication.version
            ) || case when product.cover_object_key is null then '{}'::jsonb
                 else jsonb_build_object('cover',jsonb_build_object(
                   'object_key',product.cover_object_key,
                   'storage_version_id',product.cover_storage_version_id,
                   'media_type',product.cover_media_type,
                   'width',product.cover_width,'height',product.cover_height)) end,"""


def _biblioteca(com_capa: bool) -> str:
    """A função como a 0007 a deixou, com ou sem a capa."""
    texto = _texto_da_0007()
    if not com_capa:
        return texto
    if texto.count(_ANCORA) != 1:
        raise RuntimeError("a âncora do produto não aparece exatamente uma vez")
    return texto.replace(_ANCORA, _COM_CAPA, 1)


_READY = "app_private.schema_ready()"
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_READY_ATE_0057 = "'0056_banner_do_produto','0057_banner_na_vitrine'))"
_READY_ATE_0058 = "'0056_banner_do_produto','0057_banner_na_vitrine','0058_capa_na_biblioteca'))"

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


def upgrade() -> None:
    op.execute(_biblioteca(com_capa=True))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0057)
            .replace("PARA", _READY_ATE_0058)
        )


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0058)
            .replace("PARA", _READY_ATE_0057)
        )
    op.execute(_biblioteca(com_capa=False))
