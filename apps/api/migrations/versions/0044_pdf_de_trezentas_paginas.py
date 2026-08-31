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
#: O teto do pacote, antes e depois. Um número só, e ele aparece em cinco
#: lugares do banco: duas restrições, o gatilho e a função de registro.
_PACOTE_ANTIGO = 128 * 1024 * 1024
_PACOTE_NOVO = 384 * 1024 * 1024

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


#: Cada função, e os números que ela carrega no corpo.
#:
#: Por assinatura, e não por nome: duas funções podem compartilhar o nome com
#: argumentos diferentes, e reescrever a errada passaria sem ninguém ver.
#:
#: E cada uma com as suas trocas, porque elas não carregam as mesmas coisas: o
#: gatilho conhece só o teto de bytes, a autorização só o de páginas, e o
#: registro conhece os dois. Aplicar todas as trocas a todas as funções foi o
#: primeiro rascunho desta migration — o ensaio contra uma cópia do schema de
#: produção parou nisso, que é exatamente para o que ele serve.
_PAGINAS_DE = "between 1 and 100"
_PAGINAS_PARA = "between 1 and 300"

_FUNCOES_COM_LIMITE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("app_private.authorize_entitled_pdf_page(uuid,uuid,uuid,integer)", ("paginas",)),
    (
        "app_private.register_asset_ready_candidate_v2"
        "(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb)",
        ("paginas", "pacote"),
    ),
    ("app_private.enforce_asset_pdf_page_limits()", ("pacote",)),
)

_TROCA_NUMA_FUNCAO = """
do $$
declare
  definicao text := pg_get_functiondef('ALVO'::regprocedure);
  reescrita text := replace(definicao, 'DE', 'PARA');
begin
  if reescrita = definicao then
    raise exception 'ALVO não continha % — a migration está desatualizada', 'DE';
  end if;
  execute reescrita;
end
$$;
"""


def _troca_nas_funcoes(*, paginas: tuple[str, str], pacote: tuple[str, str]) -> None:
    """Reescreve cada função com as trocas que ela de fato carrega.

    Por substituição de texto sobre a própria definição, e não recriando o corpo
    aqui: copiá-lo para dentro desta migration seria mais uma cópia do limite,
    que é o problema que ela existe para reduzir.

    E falha alto quando o texto não é encontrado. Uma substituição silenciosa
    que não substitui nada é o modo exato como o teto anterior sobreviveu: a
    migration passava, ninguém olhava, e o banco continuava recusando.
    """
    trocas = {"paginas": paginas, "pacote": pacote}
    for assinatura, quais in _FUNCOES_COM_LIMITE:
        for qual in quais:
            de, para = trocas[qual]
            op.execute(
                _TROCA_NUMA_FUNCAO.replace("ALVO", assinatura)
                .replace("DE", de)
                .replace("PARA", para)
            )


def upgrade() -> None:
    _troca_teto_de_pagina(300)
    _troca_teto_do_pacote(_PACOTE_NOVO)
    # O teto do pacote também está escrito dentro do gatilho e da função de
    # registro. Sem isto, um pacote de 200 MiB passaria pelas restrições e
    # morreria no `INSERT`: alargar pela metade só muda a porta em que o
    # vendedor bate.
    _troca_nas_funcoes(
        paginas=(_PAGINAS_DE, _PAGINAS_PARA),
        pacote=(str(_PACOTE_ANTIGO), str(_PACOTE_NOVO)),
    )


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
    _troca_teto_do_pacote(_PACOTE_ANTIGO)
    _troca_nas_funcoes(
        paginas=(_PAGINAS_PARA, _PAGINAS_DE),
        pacote=(str(_PACOTE_NOVO), str(_PACOTE_ANTIGO)),
    )
