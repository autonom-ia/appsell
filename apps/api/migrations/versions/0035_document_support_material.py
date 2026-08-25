"""Accept office and text documents as downloadable support material.

Lessons could carry a PDF or an MP4 and nothing else, so a seller with a
spreadsheet or a checklist had to convert it or leave it out. This migration
widens the accepted media set under three rules that keep the existing safety
posture intact.

Documents are support material only. They never become course content, so the
player never has to decide how to display one, and they are always handed over as
a download rather than opened inside the page. Formats that can carry macros
(``.docm``, ``.xlsm``, ``.pptm``) stay refused: the accepted office types are the
macro-free OOXML ones, and the worker proves the format by looking inside the
container rather than trusting the extension.

The media contract lived in five separate CHECK expressions and three function
bodies, none of them derived from the others, so a format added in one place was
refused in another. ``app_private.asset_media_contract_ok`` is now the single
expression the upload path consults, and the table constraints quote the same
list.

The function bodies are patched by locating them through ``pg_proc`` rather than
by naming an exact signature, because a signature literal that drifts fails at
apply time for a reason that has nothing to do with the change being made.
"""

from __future__ import annotations

from alembic import op

revision = "0035_document_support_material"
down_revision = "0034_publish_access_links"
branch_labels = None
depends_on = None

# (media type, extension, maximum bytes). Office formats are the macro-free OOXML
# ones; the macro-enabled twins are absent on purpose.
_DOCUMENT_MEDIA = (
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
        20_971_520,
    ),
    (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
        20_971_520,
    ),
    (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
        41_943_040,
    ),
    ("text/plain", "txt", 2_097_152),
    ("text/markdown", "md", 2_097_152),
    ("text/csv", "csv", 5_242_880),
    ("application/json", "json", 5_242_880),
)

_PDF_BYTES = 10_485_760
_VIDEO_BYTES = 104_857_600

_DOCUMENT_TYPES_SQL = ",".join(f"'{media}'" for media, _, _ in _DOCUMENT_MEDIA)
_DOWNLOADABLE_SQL = f"'application/pdf',{_DOCUMENT_TYPES_SQL}"
_ALL_TYPES_SQL = f"'application/pdf','video/mp4',{_DOCUMENT_TYPES_SQL}"


def _clauses(media_column: str, name_column: str, size_column: str) -> str:
    rows = [
        (
            f"({media_column}='application/pdf' and lower({name_column})~'[.]pdf$'"
            f" and {size_column} between 1 and {_PDF_BYTES})"
        ),
        (
            f"({media_column}='video/mp4' and lower({name_column})~'[.]mp4$'"
            f" and {size_column} between 1 and {_VIDEO_BYTES})"
        ),
    ]
    rows.extend(
        f"({media_column}='{media}' and lower({name_column})~'[.]{extension}$'"
        f" and {size_column} between 1 and {maximum})"
        for media, extension, maximum in _DOCUMENT_MEDIA
    )
    return "\n  or ".join(rows)


_BLOB_CLAUSES = "\n  or ".join(
    [
        f"(media_type='application/pdf' and magic_type='application/pdf'"
        f" and byte_size between 1 and {_PDF_BYTES})",
        f"(media_type='video/mp4' and magic_type='video/mp4'"
        f" and byte_size between 1 and {_VIDEO_BYTES})",
    ]
    + [
        f"(media_type='{media}' and magic_type='{media}' and byte_size between 1 and {maximum})"
        for media, _, maximum in _DOCUMENT_MEDIA
    ]
)

_CONTRACT_FUNCTION = f"""
create function app_private.asset_media_contract_ok(
  p_media_type text,
  p_file_name text,
  p_byte_size bigint
) returns boolean
language sql immutable
set search_path=pg_catalog,app_private,public,pg_temp as $$
  select p_media_type is not null and p_file_name is not null and p_byte_size is not null
    and ({_clauses("p_media_type", "p_file_name", "p_byte_size")})
$$
"""

_INTENT_ANCHOR = "or (p_media_type='video/mp4' and lower(p_file_name)~'[.]mp4$'"
_INTENT_REPLACEMENT = (
    "or app_private.asset_media_contract_ok(p_media_type,p_file_name,p_size)\n"
    "               or (p_media_type='video/mp4' and lower(p_file_name)~'[.]mp4$'"
)

# Only a PDF needs a raster manifest before promotion. Every other accepted
# format takes the plain branch, which previously named MP4 explicitly.
_FINALIZE_ANCHOR = "if upload.expected_media_type='video/mp4' then"
_FINALIZE_REPLACEMENT = "if upload.expected_media_type<>'application/pdf' then"

_TRIGGER_ANCHOR = "and blob.id=new.blob_id and blob.media_type='application/pdf'"
_TRIGGER_REPLACEMENT = f"and blob.id=new.blob_id and blob.media_type in ({_DOWNLOADABLE_SQL})"

_POLICY_ANCHOR = "and (not p_download_allowed or blob.media_type='application/pdf')"
_POLICY_REPLACEMENT = f"and (not p_download_allowed or blob.media_type in ({_DOWNLOADABLE_SQL}))"

_AUTHORIZE_ANCHOR = "and asset.media_type='application/pdf'"
_AUTHORIZE_REPLACEMENT = f"and asset.media_type in ({_DOWNLOADABLE_SQL})"

_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access','0032_platform_superadmin_catalog',"
    "'0033_pdf_study_state','0034_publish_access_links')"
)
_READY_TAIL_WITH_0035 = _READY_TAIL[:-1] + ",'0035_document_support_material')"
_READY_ANCHOR = (
    "and to_regprocedure('app_private.redeem_product_access_link"
    "(uuid,text,uuid,text,uuid)') is not null"
)
_READY_ADDITION = f"""{_READY_ANCHOR}
            and to_regprocedure(
              'app_private.asset_media_contract_ok(text,text,bigint)'
            ) is not null"""


# Written as a template rather than an f-string so the catalogue lookup is not
# assembled from interpolation; every marker below is replaced with a literal
# defined in this file.
_WIDEN_TEMPLATE = """
    do $migration$
    declare target oid; definition text; changed text; touched integer := 0;
    begin
      for target in
        select routine.oid
        from pg_proc routine
        join pg_namespace space on space.oid=routine.pronamespace
        where space.nspname='app_private' and routine.proname='__FUNCTION__'
      loop
        definition:=pg_get_functiondef(target);
        changed:=replace(definition,$old$__OLD__$old$,$new$__NEW__$new$);
        if changed<>definition then
          execute changed;
          touched:=touched+1;
        end if;
      end loop;
      if touched=0 then
        raise exception 'cannot widen __LABEL__ for documents' using errcode='55000';
      end if;
    end $migration$
    """

_DOCUMENT_GUARD_TEMPLATE = """
        do $migration$
        begin
          if exists (
            select 1 from public.asset_blob where media_type in (__TYPES__)
          ) then
            raise exception 'cannot drop document support with stored documents'
              using errcode='55000';
          end if;
        end $migration$
        """


def _widen(function_name: str, old: str, new: str, label: str) -> str:
    """Patch every overload of a function, located by name rather than signature."""
    return (
        _WIDEN_TEMPLATE.replace("__FUNCTION__", function_name)
        .replace("__OLD__", old)
        .replace("__NEW__", new)
        .replace("__LABEL__", label)
    )


def upgrade() -> None:
    statements = [
        _CONTRACT_FUNCTION,
        "revoke all on function app_private.asset_media_contract_ok(text,text,bigint) from public",
        "grant execute on function app_private.asset_media_contract_ok(text,text,bigint) "
        "to appsell_runtime",
        "alter table app_private.asset_upload drop constraint asset_upload_media_contract_check",
        f"""
alter table app_private.asset_upload add constraint asset_upload_media_contract_check check(
  {_clauses("expected_media_type", "file_name", "expected_byte_size")}
)
        """,
        "alter table public.asset_blob drop constraint asset_blob_media_contract_check",
        f"""
alter table public.asset_blob add constraint asset_blob_media_contract_check check(
  {_BLOB_CLAUSES}
)
        """,
        "alter table app_private.asset_storage_cleanup_job "
        "drop constraint asset_storage_cleanup_job_media_type_check",
        "alter table app_private.asset_storage_cleanup_job "
        "add constraint asset_storage_cleanup_job_media_type_check "
        f"check(media_type is null or media_type in ({_ALL_TYPES_SQL},'image/png'))",
        "alter table public.publication_asset "
        "drop constraint publication_asset_download_policy_check",
        "alter table public.publication_asset "
        "add constraint publication_asset_download_policy_check "
        "check(not download_allowed or ("
        f"content_role='support_material' and media_type in ({_DOWNLOADABLE_SQL})))",
        _widen(
            "create_asset_upload_intent", _INTENT_ANCHOR, _INTENT_REPLACEMENT, "the upload intent"
        ),
        _widen(
            "finalize_asset_verification",
            _FINALIZE_ANCHOR,
            _FINALIZE_REPLACEMENT,
            "the verification finaliser",
        ),
        _widen(
            "finalize_asset_verification_v2",
            _FINALIZE_ANCHOR,
            _FINALIZE_REPLACEMENT,
            "the versioned verification finaliser",
        ),
        _widen(
            "enforce_lesson_asset_draft_policy",
            _TRIGGER_ANCHOR,
            _TRIGGER_REPLACEMENT,
            "the draft policy",
        ),
        _widen(
            "set_lesson_asset_policy", _POLICY_ANCHOR, _POLICY_REPLACEMENT, "the policy mutation"
        ),
        _widen(
            "authorize_publication_asset_download",
            _AUTHORIZE_ANCHOR,
            _AUTHORIZE_REPLACEMENT,
            "the download authorisation",
        ),
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0035}$new$);
          if changed=definition then
            raise exception 'cannot extend 0016 readiness for 0035' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0035}$new$);
          if changed=definition then
            raise exception 'cannot extend 0017 readiness for 0035' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ANCHOR}$old$,$new${_READY_ADDITION}$new$);
          if changed=definition then
            raise exception 'cannot extend schema readiness for 0035' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        _DOCUMENT_GUARD_TEMPLATE.replace("__TYPES__", _DOCUMENT_TYPES_SQL),
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ADDITION}$old$,$new${_READY_ANCHOR}$new$);
          if changed=definition then
            raise exception 'cannot restore readiness from 0035' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0035}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0017 readiness from 0035' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0035}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0016 readiness from 0035' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        _widen(
            "authorize_publication_asset_download",
            _AUTHORIZE_REPLACEMENT,
            _AUTHORIZE_ANCHOR,
            "the download authorisation back",
        ),
        _widen(
            "set_lesson_asset_policy",
            _POLICY_REPLACEMENT,
            _POLICY_ANCHOR,
            "the policy mutation back",
        ),
        _widen(
            "enforce_lesson_asset_draft_policy",
            _TRIGGER_REPLACEMENT,
            _TRIGGER_ANCHOR,
            "the draft policy back",
        ),
        _widen(
            "finalize_asset_verification_v2",
            _FINALIZE_REPLACEMENT,
            _FINALIZE_ANCHOR,
            "the versioned verification finaliser back",
        ),
        _widen(
            "finalize_asset_verification",
            _FINALIZE_REPLACEMENT,
            _FINALIZE_ANCHOR,
            "the verification finaliser back",
        ),
        _widen(
            "create_asset_upload_intent",
            _INTENT_REPLACEMENT,
            _INTENT_ANCHOR,
            "the upload intent back",
        ),
        "alter table public.publication_asset "
        "drop constraint publication_asset_download_policy_check",
        "alter table public.publication_asset "
        "add constraint publication_asset_download_policy_check "
        "check(not download_allowed or ("
        "content_role='support_material' and media_type='application/pdf'))",
        "alter table app_private.asset_storage_cleanup_job "
        "drop constraint asset_storage_cleanup_job_media_type_check",
        "alter table app_private.asset_storage_cleanup_job "
        "add constraint asset_storage_cleanup_job_media_type_check "
        "check(media_type is null or media_type in "
        "('application/pdf','video/mp4','image/png'))",
        "alter table public.asset_blob drop constraint asset_blob_media_contract_check",
        """
alter table public.asset_blob add constraint asset_blob_media_contract_check check(
  (media_type='application/pdf' and magic_type='application/pdf'
    and byte_size between 1 and 10485760)
  or (media_type='video/mp4' and magic_type='video/mp4'
    and byte_size between 1 and 104857600)
)
        """,
        "alter table app_private.asset_upload drop constraint asset_upload_media_contract_check",
        """
alter table app_private.asset_upload add constraint asset_upload_media_contract_check check(
  (expected_media_type='application/pdf' and lower(file_name)~'[.]pdf$'
    and expected_byte_size between 1 and 10485760)
  or (expected_media_type='video/mp4' and lower(file_name)~'[.]mp4$'
    and expected_byte_size between 1 and 104857600)
)
        """,
        "drop function app_private.asset_media_contract_ok(text,text,bigint)",
    ]
    for statement in statements:
        op.execute(statement)
