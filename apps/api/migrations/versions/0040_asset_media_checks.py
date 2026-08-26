"""As CHECK CONSTRAINTS param de transcrever o catálogo de mídia.

A `0039` pôs o catálogo do banco numa fonte só — mas só nas **funções**. Três
CHECK CONSTRAINTS carregavam a mesma tabela transcrita de novo, e constraint não
é função: nenhuma busca em `pg_proc` as encontrava.

Elas mordem em três momentos diferentes da mesma jornada:

- `app_private.asset_upload` — ao pedir o envio
- `public.asset_blob` — ao confirmar
- `public.learning_asset` — ao publicar

Corrigir só a primeira faria o vendedor bater na segunda, e depois na terceira.

O erro que ele via era `23514` (check_violation), que o SQLAlchemy levanta como
`IntegrityError`, que a API traduz para **409 asset_conflict** e a tela mostra
como "não foi possível preparar o arquivo". Nada nessa cadeia menciona tamanho,
formato ou limite.

`asset_media_max_bytes(media_type)` é o acessório escalar que faltava: uma CHECK
não pode consultar função que devolve tabela sem subconsulta, e subconsulta é
proibida em CHECK.

O que **não** muda:

- `asset_blob` continua exigindo `magic_type = media_type` — o tipo declarado
  tem de bater com o que os bytes dizem ser;
- `learning_asset` continua aceitando só PDF e MP4, e continua exigindo o nome
  já normalizado (`^[a-z0-9][a-z0-9-]+[.]ext$`). Só o teto vira derivado.

Uma CHECK que chama função não é revalidada quando a função muda. Para alargar
um teto isso é inofensivo — nenhuma linha existente deixa de valer. Para
**apertar**, é preciso revalidar de propósito. Quem apertar um teto no futuro
precisa saber disso: está escrito aqui e no teste.
"""

from __future__ import annotations

from alembic import op

revision = "0040_asset_media_checks"
down_revision = "0039_asset_media_catalogue"
branch_labels = None
depends_on = None

_MAX_BYTES = """
    create or replace function app_private.asset_media_max_bytes(p_media_type text)
    returns bigint
    language sql
    immutable
    set search_path to 'pg_catalog', 'app_private', 'public', 'pg_temp'
    as $maxbytes$
      select max_bytes from app_private.asset_media_spec(p_media_type)
    $maxbytes$;
    alter function app_private.asset_media_max_bytes(text) owner to appsell_owner;
    revoke all on function app_private.asset_media_max_bytes(text) from public;
    grant execute on function app_private.asset_media_max_bytes(text) to appsell_runtime;
"""

_UPLOAD_CHECK = """
    alter table app_private.asset_upload
      drop constraint asset_upload_media_contract_check;
    alter table app_private.asset_upload
      add constraint asset_upload_media_contract_check
      check (app_private.asset_media_contract_ok(
        expected_media_type, file_name, expected_byte_size));
"""

# `asset_blob` não guarda nome de arquivo: o que ela confere é o tipo declarado
# contra o tipo que os bytes revelam, mais o teto. Por isso deriva o teto em vez
# de chamar o contrato inteiro.
_BLOB_CHECK = """
    alter table public.asset_blob
      drop constraint asset_blob_media_contract_check;
    alter table public.asset_blob
      add constraint asset_blob_media_contract_check
      check (
        magic_type = media_type
        and byte_size between 1 and app_private.asset_media_max_bytes(media_type)
      );
"""

# O player só sabe apresentar PDF e vídeo, e exige o nome já normalizado. Essas
# duas regras continuam; só o teto passa a ser derivado.
_LEARNING_CHECK = """
    alter table public.learning_asset
      drop constraint learning_asset_media_contract_check;
    alter table public.learning_asset
      add constraint learning_asset_media_contract_check
      check (
        media_type in ('application/pdf', 'video/mp4')
        and file_name ~ ('^[a-z0-9][a-z0-9-]+[.]'
          || app_private.asset_media_extension(media_type) || '$')
        and byte_size between 1 and app_private.asset_media_max_bytes(media_type)
      );
"""

_EXTENSION = """
    create or replace function app_private.asset_media_extension(p_media_type text)
    returns text
    language sql
    immutable
    set search_path to 'pg_catalog', 'app_private', 'public', 'pg_temp'
    as $ext$
      select extension from app_private.asset_media_spec(p_media_type)
    $ext$;
    alter function app_private.asset_media_extension(text) owner to appsell_owner;
    revoke all on function app_private.asset_media_extension(text) from public;
    grant execute on function app_private.asset_media_extension(text) to appsell_runtime;
"""

_UPLOAD_CHECK_0039 = """
    alter table app_private.asset_upload
      drop constraint asset_upload_media_contract_check;
    alter table app_private.asset_upload
      add constraint asset_upload_media_contract_check
      check (
        (expected_media_type='application/pdf' and lower(file_name) ~ '[.]pdf$' and expected_byte_size between 1 and 10485760)
        or (expected_media_type='video/mp4' and lower(file_name) ~ '[.]mp4$' and expected_byte_size between 1 and 104857600)
        or (expected_media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document' and lower(file_name) ~ '[.]docx$' and expected_byte_size between 1 and 20971520)
        or (expected_media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' and lower(file_name) ~ '[.]xlsx$' and expected_byte_size between 1 and 20971520)
        or (expected_media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation' and lower(file_name) ~ '[.]pptx$' and expected_byte_size between 1 and 41943040)
        or (expected_media_type='text/plain' and lower(file_name) ~ '[.]txt$' and expected_byte_size between 1 and 2097152)
        or (expected_media_type='text/markdown' and lower(file_name) ~ '[.]md$' and expected_byte_size between 1 and 2097152)
        or (expected_media_type='text/csv' and lower(file_name) ~ '[.]csv$' and expected_byte_size between 1 and 5242880)
        or (expected_media_type='application/json' and lower(file_name) ~ '[.]json$' and expected_byte_size between 1 and 5242880)
      );
"""

_BLOB_CHECK_0039 = """
    alter table public.asset_blob
      drop constraint asset_blob_media_contract_check;
    alter table public.asset_blob
      add constraint asset_blob_media_contract_check
      check (
        (media_type='application/pdf' and magic_type='application/pdf' and byte_size between 1 and 10485760)
        or (media_type='video/mp4' and magic_type='video/mp4' and byte_size between 1 and 104857600)
        or (media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document' and magic_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document' and byte_size between 1 and 20971520)
        or (media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' and magic_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' and byte_size between 1 and 20971520)
        or (media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation' and magic_type='application/vnd.openxmlformats-officedocument.presentationml.presentation' and byte_size between 1 and 41943040)
        or (media_type='text/plain' and magic_type='text/plain' and byte_size between 1 and 2097152)
        or (media_type='text/markdown' and magic_type='text/markdown' and byte_size between 1 and 2097152)
        or (media_type='text/csv' and magic_type='text/csv' and byte_size between 1 and 5242880)
        or (media_type='application/json' and magic_type='application/json' and byte_size between 1 and 5242880)
      );
"""

_LEARNING_CHECK_0039 = """
    alter table public.learning_asset
      drop constraint learning_asset_media_contract_check;
    alter table public.learning_asset
      add constraint learning_asset_media_contract_check
      check (
        (media_type='application/pdf' and file_name ~ '^[a-z0-9][a-z0-9-]+[.]pdf$' and byte_size between 1 and 10485760)
        or (media_type='video/mp4' and file_name ~ '^[a-z0-9][a-z0-9-]+[.]mp4$' and byte_size between 1 and 104857600)
      );
"""

_DROP_HELPERS = """
    drop function if exists app_private.asset_media_max_bytes(text);
    drop function if exists app_private.asset_media_extension(text);
"""

_READY_TAIL = "'0037_product_cover','0038_player_hides_materials','0039_asset_media_catalogue')"
_READY_TAIL_WITH_0040 = _READY_TAIL[:-1] + ",'0040_asset_media_checks')"


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
    op.execute(_MAX_BYTES)
    op.execute(_EXTENSION)
    op.execute(_UPLOAD_CHECK)
    op.execute(_BLOB_CHECK)
    op.execute(_LEARNING_CHECK)
    op.execute(_ready(_READY_TAIL, _READY_TAIL_WITH_0040, "cannot extend readiness for 0040 in"))


def downgrade() -> None:
    op.execute(_ready(_READY_TAIL_WITH_0040, _READY_TAIL, "cannot restore readiness from 0040 in"))
    op.execute(_LEARNING_CHECK_0039)
    op.execute(_BLOB_CHECK_0039)
    op.execute(_UPLOAD_CHECK_0039)
    op.execute(_DROP_HELPERS)
