"""O catálogo de mídia passa a ter uma fonte só dentro do banco.

O limite de bytes por formato estava transcrito em SQL, e transcrito duas
vezes: uma no catálogo (`asset_media_contract_ok`) e outra cravada dentro de
`create_asset_upload_intent`, que já chamava o catálogo na linha seguinte. As
duas diziam 10 MiB para PDF.

Isso não é feiura: em 26/08/2026 o limite do PDF subiu para 100 MiB no
navegador e na API, o deploy passou verde, e o banco continuou recusando com
`errcode 22023` — que a API traduz para 422 e a tela mostra como "não foi
possível preparar o arquivo". Três camadas concordando e uma discordando, sem
nada acusando a divergência.

A correção é estrutural, não um número novo:

- `asset_media_spec(media_type)` passa a ser a única tabela de formatos do
  banco, com extensão e teto de bytes;
- `asset_media_contract_ok` deriva dela em vez de repetir os nove formatos;
- `create_asset_upload_intent` perde a cópia inline da regra do PDF e chama só
  o catálogo.

O trio que existia no `intent` — PDF até 10 MiB **ou** catálogo **ou** MP4 até
100 MiB — era redundante: as duas pontas já estavam dentro do catálogo, e por
serem `or` nunca restringiram nada além dele. Some sem mudar semântica.

O patch do `intent` usa a definição viva, não o texto da migration que criou a
função: migrations posteriores já a reescreveram, e ancorar no texto original
falharia em silêncio.

A verificação de que navegador, API e banco continuam concordando é do teste
`test_postgres_asset_media_catalogue.py`, que lê os três e compara formato por
formato. Ele é a rede que faltou aqui.
"""

from __future__ import annotations

from alembic import op

revision = "0039_asset_media_catalogue"
down_revision = "0038_player_hides_materials"
branch_labels = None
depends_on = None

# A tabela de formatos, agora em um lugar só. Espelha
# `apps/api/src/appsell_api/asset_media.py` e `lib/asset-media.ts`.
_SPEC = """
    create or replace function app_private.asset_media_spec(p_media_type text)
    returns table (extension text, max_bytes bigint)
    language sql
    immutable
    set search_path to 'pg_catalog', 'app_private', 'public', 'pg_temp'
    as $spec$
      select c.extension, c.max_bytes
      from (values
        ('application/pdf', 'pdf', 104857600::bigint),
        ('video/mp4', 'mp4', 104857600::bigint),
        ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          'docx', 20971520::bigint),
        ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'xlsx', 20971520::bigint),
        ('application/vnd.openxmlformats-officedocument.presentationml.presentation',
          'pptx', 41943040::bigint),
        ('text/plain', 'txt', 2097152::bigint),
        ('text/markdown', 'md', 2097152::bigint),
        ('text/csv', 'csv', 5242880::bigint),
        ('application/json', 'json', 5242880::bigint)
      ) as c(media_type, extension, max_bytes)
      where c.media_type = p_media_type
    $spec$;
    alter function app_private.asset_media_spec(text) owner to appsell_owner;
    revoke all on function app_private.asset_media_spec(text) from public;
    grant execute on function app_private.asset_media_spec(text) to appsell_runtime;
"""

# O catálogo como estava antes de 0039: os nove formatos transcritos, com o PDF
# em 10 MiB. Existe só para o `downgrade` devolver o banco ao estado anterior
# sem depender de `asset_media_spec`, que ele mesmo remove.
_CONTRACT_0038 = """
    create or replace function app_private.asset_media_contract_ok(
      p_media_type text, p_file_name text, p_byte_size bigint
    )
    returns boolean
    language sql
    immutable
    set search_path to 'pg_catalog', 'app_private', 'public', 'pg_temp'
    as $contract$
      select p_media_type is not null and p_file_name is not null
        and p_byte_size is not null
        and ((p_media_type='application/pdf' and lower(p_file_name)~'[.]pdf$' and p_byte_size between 1 and 10485760)
      or (p_media_type='video/mp4' and lower(p_file_name)~'[.]mp4$' and p_byte_size between 1 and 104857600)
      or (p_media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document' and lower(p_file_name)~'[.]docx$' and p_byte_size between 1 and 20971520)
      or (p_media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' and lower(p_file_name)~'[.]xlsx$' and p_byte_size between 1 and 20971520)
      or (p_media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation' and lower(p_file_name)~'[.]pptx$' and p_byte_size between 1 and 41943040)
      or (p_media_type='text/plain' and lower(p_file_name)~'[.]txt$' and p_byte_size between 1 and 2097152)
      or (p_media_type='text/markdown' and lower(p_file_name)~'[.]md$' and p_byte_size between 1 and 2097152)
      or (p_media_type='text/csv' and lower(p_file_name)~'[.]csv$' and p_byte_size between 1 and 5242880)
      or (p_media_type='application/json' and lower(p_file_name)~'[.]json$' and p_byte_size between 1 and 5242880))
    $contract$;
    alter function app_private.asset_media_contract_ok(text,text,bigint)
      owner to appsell_owner;
    revoke all on function app_private.asset_media_contract_ok(text,text,bigint)
      from public;
    grant execute on function app_private.asset_media_contract_ok(text,text,bigint)
      to appsell_runtime;
"""

_DROP_SPEC = "drop function if exists app_private.asset_media_spec(text);"

# Mesma assinatura e mesmas propriedades de antes: `schema_ready` confere a
# assinatura, e trocá-la quebraria a checagem de prontidão do esquema.
_CONTRACT = """
    create or replace function app_private.asset_media_contract_ok(
      p_media_type text, p_file_name text, p_byte_size bigint
    )
    returns boolean
    language sql
    immutable
    set search_path to 'pg_catalog', 'app_private', 'public', 'pg_temp'
    as $contract$
      select exists (
        select 1
        from app_private.asset_media_spec(p_media_type) as s
        where p_file_name is not null
          and p_byte_size is not null
          and lower(p_file_name) ~ ('[.]' || s.extension || '$')
          and p_byte_size between 1 and s.max_bytes
      )
    $contract$;
    alter function app_private.asset_media_contract_ok(text,text,bigint)
      owner to appsell_owner;
    revoke all on function app_private.asset_media_contract_ok(text,text,bigint)
      from public;
    grant execute on function app_private.asset_media_contract_ok(text,text,bigint)
      to appsell_runtime;
"""

_INTENT_ANCHOR = """             or p_file_name~'[[:cntrl:]]' or not (
               (p_media_type='application/pdf' and lower(p_file_name)~'[.]pdf$'
                 and p_size between 1 and 10485760)
               or app_private.asset_media_contract_ok(p_media_type,p_file_name,p_size)
               or (p_media_type='video/mp4' and lower(p_file_name)~'[.]mp4$'
                 and p_size between 1 and 104857600)
             )
"""

_INTENT_REPLACEMENT = """             or p_file_name~'[[:cntrl:]]'
             or not app_private.asset_media_contract_ok(p_media_type,p_file_name,p_size)
"""

_READY_TAIL = (
    "'0035_document_support_material','0036_lesson_material_links',"
    "'0037_product_cover','0038_player_hides_materials')"
)
_READY_TAIL_WITH_0039 = _READY_TAIL[:-1] + ",'0039_asset_media_catalogue')"


def _patch_intent(old: str, new: str, label: str) -> str:
    """Reescreve `create_asset_upload_intent` a partir da definição viva.

    Falhar alto quando a âncora não casa é de propósito: uma migration que não
    encontra o que veio corrigir e segue adiante deixa o banco discordando das
    outras camadas — que é exatamente o defeito que ela existe para fechar.
    """
    return f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef(
            'app_private.create_asset_upload_intent('
            'uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,text,text,text,bigint,'
            'text,text,text,text,uuid,text,uuid)'::regprocedure
          );
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
    op.execute(_SPEC)
    op.execute(_CONTRACT)
    op.execute(
        _patch_intent(
            _INTENT_ANCHOR,
            _INTENT_REPLACEMENT,
            "cannot route the upload intent through the media catalogue",
        )
    )
    op.execute(_ready(_READY_TAIL, _READY_TAIL_WITH_0039, "cannot extend readiness for 0039 in"))


def downgrade() -> None:
    """Devolve o banco a 0038, na ordem inversa.

    O contrato volta a transcrever os nove formatos **antes** de
    `asset_media_spec` sair: enquanto ele depender da função, removê-la primeiro
    deixaria a validação de upload quebrada no meio do caminho.
    """
    op.execute(_ready(_READY_TAIL_WITH_0039, _READY_TAIL, "cannot restore readiness from 0039 in"))
    op.execute(
        _patch_intent(
            _INTENT_REPLACEMENT,
            _INTENT_ANCHOR,
            "cannot restore the inline media rule in the upload intent",
        )
    )
    op.execute(_CONTRACT_0038)
    op.execute(_DROP_SPEC)
