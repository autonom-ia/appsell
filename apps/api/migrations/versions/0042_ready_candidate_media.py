"""O documento chega ao fim do caminho de verificação.

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

## O que esta migration NÃO faz

O teto de 100 páginas do PDF é outro defeito, achado enquanto este era
corrigido: o worker rasteriza até 300 páginas desde 26/08/2026 e o banco
continua em 100. Não cabe aqui. Ele aparece em **nove** lugares do esquema — o
manifesto, as páginas, os dois candidatos, o gatilho de totais, a autorização de
página do aluno, a anotação e o marcador — e em três módulos Python. Alargar
parte deles só mudaria a porta em que o vendedor bate. Fica em #293.

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

_MEDIA_ANCHOR = "             or p_ready_media not in ('application/pdf','video/mp4')\n"
_MEDIA_REPLACEMENT = "             or app_private.asset_media_extension(p_ready_media) is null\n"

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
    op.execute(_ready(_READY_TAIL, _READY_TAIL_WITH_0042, "cannot extend readiness for 0042 in"))


def downgrade() -> None:
    op.execute(_ready(_READY_TAIL_WITH_0042, _READY_TAIL, "cannot restore readiness from 0042 in"))
    op.execute(
        _patch(
            _LEGACY_SIGNATURE,
            _MEDIA_REPLACEMENT,
            _MEDIA_ANCHOR,
            "cannot restore the closed media set in the ready candidate",
        )
    )
