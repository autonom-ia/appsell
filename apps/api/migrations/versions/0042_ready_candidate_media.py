"""O documento chega ao fim do caminho, e o PDF longo para de ser recusado.

## A última porta

`register_asset_ready_candidate_v1_legacy` era a única função do caminho de
verificação que restringia formato:

    or p_ready_media not in ('application/pdf','video/mp4')
      then raise exception 'invalid ready asset candidate' using errcode='22023'

O intent aceitava os nove formatos, o confirm aceitava, o `finalize` aceitava, a
constraint de `asset_blob` aceitava, a de `asset_storage_cleanup_job` aceitava —
e o registro do candidato recusava. **DOCX, XLSX, PPTX, TXT, MD, CSV e JSON
nunca puderam ser registrados**, desde que entraram no catálogo.

O arquivo subia, passava no antivírus, era verificado em 0,1 s e morria ali. E o
worker engolia o erro:

    except DBAPIError:
        ...  # tenta de novo, compensa, return — sem log nenhum

Três camadas de silêncio: a exceção não virava log, o registro não virava
`asset_record`, e a tela dizia "a verificação está demorando mais que o
esperado" — que promete que vai resolver sozinho.

A correção deriva do catálogo criado na `0039` em vez de listar formato de novo:
`asset_media_extension(p_ready_media) is null` recusa exatamente o que o
catálogo não conhece, e passa a acompanhá-lo sozinho.

## O PDF de 120 páginas

Achado enquanto isto era corrigido, e do mesmo tipo. Em 26/08/2026 o worker
passou a rasterizar até 300 páginas e 512 MiB de derivado. O banco continuou em
100 páginas e 128 MiB:

    or jsonb_array_length(p_pdf_pages) not between 1 and 100 then
    if page_count<>expected_page-1 or page_total not between 1 and 134217728 then

O playbook de 83 páginas passou por sorte. Um documento de 120 seria
rasterizado inteiro — gastando o lease — e recusado no fim com `invalid PDF
raster manifest`.

Aqui os números continuam transcritos, porque criar um catálogo de raster no
banco para dois valores usados em uma função só seria mais estrutura do que
regra. O que fecha a divergência são os dois testes que leem `prosrc` e comparam
com `MAX_PDF_PAGES` e `MAX_PDF_DERIVATIVE_BYTES` — mesma rede que já guarda a
faixa de lease do worker.

## Por que patch com âncora

Estas funções já foram reescritas por migrations posteriores às que as criaram.
Ancorar no texto original falharia em silêncio; ancorar na definição viva e
levantar exceção quando a âncora não casa é o padrão da `0038`/`0039`.
"""

from __future__ import annotations

from alembic import op

revision = "0042_ready_candidate_media"
down_revision = "0041_material_link_checks"
branch_labels = None
depends_on = None

_LEGACY_SIGNATURE = (
    "'app_private.register_asset_ready_candidate_v1_legacy("
    "uuid,uuid,uuid,text,text,text,bigint,text,text,text,text)'::regprocedure"
)
_V2_SIGNATURE = (
    "'app_private.register_asset_ready_candidate_v2("
    "uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb)'::regprocedure"
)

_MEDIA_ANCHOR = "             or p_ready_media not in ('application/pdf','video/mp4')\n"
_MEDIA_REPLACEMENT = "             or app_private.asset_media_extension(p_ready_media) is null\n"

_PAGES_ANCHOR = "             or jsonb_array_length(p_pdf_pages) not between 1 and 100 then\n"
_PAGES_REPLACEMENT = "             or jsonb_array_length(p_pdf_pages) not between 1 and 300 then\n"

_TOTAL_ANCHOR = (
    "          if page_count<>expected_page-1 or page_total not between 1 and 134217728 then\n"
)
_TOTAL_REPLACEMENT = (
    "          if page_count<>expected_page-1 or page_total not between 1 and 536870912 then\n"
)

_READY_TAIL = "'0040_asset_media_checks','0041_material_link_checks')"
_READY_TAIL_WITH_0042 = _READY_TAIL[:-1] + ",'0042_ready_candidate_media')"


def _patch(signature: str, old: str, new: str, label: str) -> str:
    """Reescreve uma função a partir da definição viva.

    Falhar alto quando a âncora não casa é de propósito: uma migration que não
    encontra o que veio corrigir e segue adiante deixa o banco discordando das
    outras camadas — que é o defeito que ela existe para fechar.
    """
    return f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef({signature});
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label}' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """


def _ready(old: str, new: str, label: str) -> str:
    return f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label} 0016' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label} 0017' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """


def upgrade() -> None:
    op.execute(
        _patch(
            _LEGACY_SIGNATURE,
            _MEDIA_ANCHOR,
            _MEDIA_REPLACEMENT,
            "cannot route the ready candidate through the media catalogue",
        )
    )
    op.execute(
        _patch(
            _V2_SIGNATURE,
            _PAGES_ANCHOR,
            _PAGES_REPLACEMENT,
            "cannot widen the PDF raster page limit",
        )
    )
    op.execute(
        _patch(
            _V2_SIGNATURE,
            _TOTAL_ANCHOR,
            _TOTAL_REPLACEMENT,
            "cannot widen the PDF raster byte limit",
        )
    )
    op.execute(_ready(_READY_TAIL, _READY_TAIL_WITH_0042, "cannot extend readiness for 0042 in"))


def downgrade() -> None:
    op.execute(_ready(_READY_TAIL_WITH_0042, _READY_TAIL, "cannot restore readiness from 0042 in"))
    op.execute(
        _patch(
            _V2_SIGNATURE,
            _TOTAL_REPLACEMENT,
            _TOTAL_ANCHOR,
            "cannot restore the PDF raster byte limit",
        )
    )
    op.execute(
        _patch(
            _V2_SIGNATURE,
            _PAGES_REPLACEMENT,
            _PAGES_ANCHOR,
            "cannot restore the PDF raster page limit",
        )
    )
    op.execute(
        _patch(
            _LEGACY_SIGNATURE,
            _MEDIA_REPLACEMENT,
            _MEDIA_ANCHOR,
            "cannot restore the closed media set in the ready candidate",
        )
    )
