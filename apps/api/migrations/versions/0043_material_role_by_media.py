"""O papel do material deixa de ser uma pergunta e passa a vir do formato.

## O que estava errado

`lesson_asset_draft.content_role` tem `default 'course_content'`, e quem insere
a linha é o **worker**, no `finalize` — não a requisição do vendedor. Então todo
material nascia como conteúdo protegido, sem download, inclusive um `.md` de
10 KiB que só existe para o aluno baixar.

A tela compensava com um `<select>` "Uso deste material" em cada card. Ou seja:
o sistema perguntava ao vendedor uma coisa que ele já sabia. O catálogo marca
documento como `supportMaterialOnly` desde a `0035`, e **ninguém consultava esse
campo** — `is_document_media` não tem um único chamador no servidor.

E a pergunta chegava tarde: o painel só conseguiria responder por conta própria
se o polling chegasse ao fim, e ele desiste em 15 segundos. Um PDF de 12 MB
nunca chega lá. Foi assim que o `.md` do vendedor nasceu como conteúdo
protegido em 27/08/2026, e ele teve de corrigir na mão.

## A regra, nomeada

`app_private.player_renders_media(media_type)` responde a pergunta que de fato
decide: **o player sabe apresentar isto dentro da área do aluno?**

- sabe → PDF (leitor paginado) e MP4 (player) → `course_content`
- não sabe → os sete documentos → `support_material`, e o aluno baixa

Não é o catálogo de tamanhos transcrito de novo: é a mesma regra que
`learning_asset_media_contract_check` já declara desde a `0017`, agora com nome
e um lugar só. A constraint continua onde está; quem quiser unificá-la com esta
função depois tem por onde começar.

O PDF continua podendo virar anexo — aí é escolha legítima do vendedor, feita
pelo painel em que ele clica, e aplicada por `set_lesson_asset_policy`, que já
existe e não muda aqui.

## O que NÃO muda

- linhas já gravadas ficam como estão. Quem já classificou material na mão não
  vê a escolha ser desfeita por uma migration.
- `download_allowed` para documento nasce `true`, que é o único valor que faz
  sentido: `set_lesson_asset_policy` já recusa download em `course_content`, e
  um documento que o aluno não pode baixar não serve para nada.
"""

from __future__ import annotations

from alembic import op

revision = "0043_material_role_by_media"
down_revision = "0042_ready_candidate_media"
branch_labels = None
depends_on = None

_FINALIZE_SIGNATURE = (
    "'app_private.finalize_asset_verification_v1_legacy("
    "uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,"
    "uuid,text,uuid)'::regprocedure"
)

# A pergunta que decide o papel, com nome. Deriva do catalogo da 0039: o que o
# catalogo nao conhece nao e apresentavel, e o que ele conhece so e apresentavel
# se o player tiver como desenhar — leitor de PDF ou player de video.
_RENDERS = """
    create or replace function app_private.player_renders_media(p_media_type text)
    returns boolean
    language sql
    immutable
    set search_path to 'pg_catalog', 'app_private', 'public', 'pg_temp'
    as $renders$
      select exists (
        select 1 from app_private.asset_media_spec(p_media_type) as s
        where s.extension in ('pdf', 'mp4')
      )
    $renders$;
    alter function app_private.player_renders_media(text) owner to appsell_owner;
    revoke all on function app_private.player_renders_media(text) from public;
    grant execute on function app_private.player_renders_media(text) to appsell_runtime;
"""

# A outra metade da regra: o aluno recebe o arquivo inteiro? O PDF sim (baixar e
# opcional), o video nao — `set_lesson_asset_policy` ja recusa download em video.
# Deriva do catalogo da 0039 pelo mesmo caminho de `player_renders_media`, e o
# teste de espelho amarra os dois lados ao catalogo Python.
_DOWNLOADABLE = """
    create or replace function app_private.asset_media_downloadable(p_media_type text)
    returns boolean
    language sql
    immutable
    set search_path to 'pg_catalog', 'app_private', 'public', 'pg_temp'
    as $downloadable$
      select exists (
        select 1 from app_private.asset_media_spec(p_media_type) as s
        where s.extension <> 'mp4'
      )
    $downloadable$;
    alter function app_private.asset_media_downloadable(text) owner to appsell_owner;
    revoke all on function app_private.asset_media_downloadable(text) from public;
    grant execute on function app_private.asset_media_downloadable(text) to appsell_runtime;
"""

_DROP_RENDERS = (
    "drop function if exists app_private.player_renders_media(text);"
    "drop function if exists app_private.asset_media_downloadable(text);"
)

_INSERT_ANCHOR = """          insert into lesson_asset_draft(seller_organization_id,catalog_product_id,course_id,
            module_id,lesson_id,asset_id,asset_version,blob_id,position,status)
          values(p_seller,candidate.catalog_product_id,candidate.course_id,candidate.module_id,
            candidate.lesson_id,candidate.asset_id,candidate.reserved_version,
            candidate.blob_id,next_position,'active')
          on conflict(seller_organization_id,lesson_id,asset_id) do update set
            asset_version=excluded.asset_version,blob_id=excluded.blob_id,status='active',
            updated_at=clock_timestamp();
"""

# O `do update` preserva a classificacao do vendedor: reenviar um arquivo nao
# pode desfaze-la. Mas preservar as cegas grava estado invalido — substituir um
# `.md` baixavel por um MP4 manteria `download_allowed=true` num video, e a
# constraint da tabela nao pega isso: ela so exige que download implique
# `support_material`, sem olhar a midia.
#
# Entao preserva o que continua valendo e normaliza o resto: um formato que o
# player nao apresenta nunca fica como conteudo do curso, e um formato que o
# aluno nao baixa nunca fica com download ligado.
_INSERT_REPLACEMENT = """          insert into lesson_asset_draft(seller_organization_id,catalog_product_id,course_id,
            module_id,lesson_id,asset_id,asset_version,blob_id,position,status,
            content_role,download_allowed)
          values(p_seller,candidate.catalog_product_id,candidate.course_id,candidate.module_id,
            candidate.lesson_id,candidate.asset_id,candidate.reserved_version,
            candidate.blob_id,next_position,'active',
            case when app_private.player_renders_media(p_ready_media)
              then 'course_content' else 'support_material' end,
            not app_private.player_renders_media(p_ready_media))
          on conflict(seller_organization_id,lesson_id,asset_id) do update set
            asset_version=excluded.asset_version,blob_id=excluded.blob_id,status='active',
            content_role=case when app_private.player_renders_media(p_ready_media)
              then lesson_asset_draft.content_role else 'support_material' end,
            download_allowed=lesson_asset_draft.download_allowed
              and app_private.asset_media_downloadable(p_ready_media)
              and (not app_private.player_renders_media(p_ready_media)
                or lesson_asset_draft.content_role='support_material'),
            updated_at=clock_timestamp();
"""

_READY_TAIL = "'0041_material_link_checks','0042_ready_candidate_media')"
_READY_TAIL_WITH_0043 = _READY_TAIL[:-1] + ",'0043_material_role_by_media')"


def _patch(signature: str, old: str, new: str, label: str) -> str:
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
    op.execute(_RENDERS)
    op.execute(_DOWNLOADABLE)
    op.execute(
        _patch(
            _FINALIZE_SIGNATURE,
            _INSERT_ANCHOR,
            _INSERT_REPLACEMENT,
            "cannot derive the material role from the media type",
        )
    )
    op.execute(_ready(_READY_TAIL, _READY_TAIL_WITH_0043, "cannot extend readiness for 0043 in"))


def downgrade() -> None:
    op.execute(_ready(_READY_TAIL_WITH_0043, _READY_TAIL, "cannot restore readiness from 0043 in"))
    op.execute(
        _patch(
            _FINALIZE_SIGNATURE,
            _INSERT_REPLACEMENT,
            _INSERT_ANCHOR,
            "cannot restore the default material role",
        )
    )
    op.execute(_DROP_RENDERS)
