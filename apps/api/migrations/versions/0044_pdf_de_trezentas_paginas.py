"""O teto de páginas do PDF sobe de 100 para 300.

O banco tinha a própria cópia do limite, em oito lugares: seis restrições e duas
funções. Enquanto elas dissessem 100, subir o número no código só mudaria onde a
recusa acontece — do envio para o `INSERT`, com o vendedor vendo um erro pior.

Vem junto o teto do pacote de páginas rasterizadas. Ele era de 128 MiB, o que
comportava 300 páginas na média medida (243 KiB), mas não no pior caso real
(642 KiB numa página do playbook do vendedor, que daria 193 MiB). Passa a 384
MiB: o dobro do pior caso, e ainda abaixo dos 512 MiB que o código já permitia
para o pacote.

Medições que sustentam o 300, feitas em produção com o playbook real:
rasterizar uma página a 150 dpi custa 0,73 s e 83 MiB de pico — e os 83 MiB são
constantes, porque o `pdftoppm` desenha uma página de cada vez. Trezentas
páginas são 3,7 minutos, uma vez, no envio. O que impede subir mais não é
memória nem disco: é que enquanto isso corre, o mesmo lugar do serviço isolado
atende a leitura de links de todo mundo (#320).

Revision ID: 0044_pdf_de_trezentas_paginas
Revises: 0043_material_role_by_media
"""

from __future__ import annotations

from alembic import op

revision = "0044_pdf_de_trezentas_paginas"
down_revision = "0043_material_role_by_media"
branch_labels = None
depends_on = None

#: As restrições que transcreviam o teto de páginas, e o valor de cada lado.
_PAGE_LIMITS: tuple[tuple[str, str, str], ...] = (
    ("public.asset_pdf_raster", "asset_pdf_raster_page_count_check", "page_count"),
    ("public.asset_pdf_page", "asset_pdf_page_page_number_check", "page_number"),
    (
        "app_private.asset_pdf_raster_candidate",
        "asset_pdf_raster_candidate_page_count_check",
        "page_count",
    ),
    (
        "app_private.asset_pdf_page_candidate",
        "asset_pdf_page_candidate_page_number_check",
        "page_number",
    ),
    (
        "app_private.learning_pdf_annotation",
        "learning_pdf_annotation_page_number_check",
        "page_number",
    ),
    (
        "app_private.learning_pdf_bookmark",
        "learning_pdf_bookmark_page_number_check",
        "page_number",
    ),
)

_RASTER_TOTAL = ("public.asset_pdf_raster", "asset_pdf_raster_total_byte_size_check")
_RASTER_CANDIDATE_TOTAL = (
    "app_private.asset_pdf_raster_candidate",
    "asset_pdf_raster_candidate_total_byte_size_check",
)


def _troca_teto_de_pagina(novo: int) -> None:
    for tabela, restricao, coluna in _PAGE_LIMITS:
        op.execute(f"alter table {tabela} drop constraint if exists {restricao}")
        op.execute(
            f"alter table {tabela} add constraint {restricao} "
            f"check ({coluna} >= 1 and {coluna} <= {novo})"
        )


def _troca_teto_do_pacote(novo_bytes: int) -> None:
    for tabela, restricao in (_RASTER_TOTAL, _RASTER_CANDIDATE_TOTAL):
        op.execute(f"alter table {tabela} drop constraint if exists {restricao}")
        op.execute(
            f"alter table {tabela} add constraint {restricao} "
            f"check (total_byte_size >= 1 and total_byte_size <= {novo_bytes})"
        )


_TROCA_NAS_FUNCOES = """
do $$
declare
  definicao text;
begin
  for definicao in
    select pg_get_functiondef(p.oid)
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'app_private'
      and p.proname in (
        'authorize_entitled_pdf_page',
        'register_asset_ready_candidate_v2'
      )
  loop
    execute replace(definicao, 'between 1 and DE', 'between 1 and PARA');
  end loop;
end
$$;
"""


def _troca_nas_funcoes(de: int, para: int) -> None:
    """As duas funções que carregavam o número escrito no corpo.

    Reescritas por substituição de texto sobre a própria definição, e não
    recriadas aqui: copiar o corpo delas para dentro desta migration seria uma
    nona cópia do limite, que é o problema que ela existe para reduzir.
    """
    op.execute(_TROCA_NAS_FUNCOES.replace("DE", str(de)).replace("PARA", str(para)))


def upgrade() -> None:
    _troca_teto_de_pagina(300)
    _troca_teto_do_pacote(384 * 1024 * 1024)
    _troca_nas_funcoes(100, 300)


def downgrade() -> None:
    # Só volta se nada tiver passado do teto antigo — descer com um documento de
    # 200 páginas gravado deixaria a tabela inconsistente com a própria regra.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from public.asset_pdf_page where page_number > 100)
             or exists (select 1 from public.asset_pdf_raster where page_count > 100)
          then
            raise exception 'existem PDFs acima de 100 páginas: descer o teto os deixaria fora da regra';
          end if;
        end
        $$;
        """
    )
    _troca_teto_de_pagina(100)
    _troca_teto_do_pacote(128 * 1024 * 1024)
    _troca_nas_funcoes(300, 100)
