from __future__ import annotations

import os

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="isolated PostgreSQL environment is required")

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCM = "application/vnd.ms-word.document.macroEnabled.12"


def contract(media_type: str, file_name: str, byte_size: int) -> bool:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select app_private.asset_media_contract_ok(%s,%s,%s)",
            (media_type, file_name, byte_size),
        ).fetchone()
    assert row is not None
    return bool(row[0])


def definition(name: str) -> str:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select pg_get_functiondef(routine.oid) from pg_proc routine "
            "join pg_namespace space on space.oid=routine.pronamespace "
            "where space.nspname='app_private' and routine.proname=%s",
            (name,),
        ).fetchall()
    assert rows, f"function {name} is missing"
    return "\n".join(str(row[0]) for row in rows)


def constraint(table: str, name: str) -> str:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select pg_get_constraintdef(constraint_row.oid) from pg_constraint constraint_row "
            "where constraint_row.conname=%s and constraint_row.conrelid=%s::regclass",
            (name, table),
        ).fetchone()
    assert row is not None, f"constraint {name} is missing"
    return str(row[0])


@pytest.mark.parametrize(
    ("media_type", "file_name", "byte_size"),
    [
        (DOCX, "roteiro.docx", 1_000),
        (DOCX, "ROTEIRO.DOCX", 20_971_520),
        (XLSX, "comparativo.xlsx", 1),
        (PPTX, "aula.pptx", 41_943_040),
        ("text/plain", "notas.txt", 2_097_152),
        ("text/markdown", "leia-me.md", 10),
        ("text/csv", "planilha.csv", 5_242_880),
        ("application/json", "dados.json", 10),
        ("application/pdf", "apostila.pdf", 10_485_760),
        ("video/mp4", "aula.mp4", 104_857_600),
    ],
)
def test_the_contract_accepts_every_supported_format(
    media_type: str, file_name: str, byte_size: int
) -> None:
    assert contract(media_type, file_name, byte_size) is True


@pytest.mark.parametrize(
    ("media_type", "file_name", "byte_size", "why"),
    [
        (DOCM, "macro.docm", 1_000, "macro enabled office format"),
        (DOCX, "macro.docm", 1_000, "extension disagrees with the media type"),
        (DOCX, "roteiro.pdf", 1_000, "extension belongs to another format"),
        (DOCX, "roteiro.docx", 20_971_521, "one byte over its ceiling"),
        (PPTX, "aula.pptx", 41_943_041, "one byte over its ceiling"),
        ("text/plain", "notas.txt", 2_097_153, "one byte over its ceiling"),
        ("application/pdf", "apostila.pdf", 10_485_761, "pdf ceiling unchanged"),
        ("application/zip", "pacote.zip", 10, "container format never accepted"),
        ("text/html", "pagina.html", 10, "markup never accepted"),
        (DOCX, "roteiro.docx", 0, "empty upload"),
    ],
)
def test_the_contract_refuses_what_it_must(
    media_type: str, file_name: str, byte_size: int, why: str
) -> None:
    assert contract(media_type, file_name, byte_size) is False, why


def test_the_upload_and_blob_constraints_quote_the_document_formats() -> None:
    """The database refuses a document the API would accept, or the reverse, if
    these two drift apart from the contract function."""
    upload = constraint("app_private.asset_upload", "asset_upload_media_contract_check")
    blob = constraint("public.asset_blob", "asset_blob_media_contract_check")
    for media_type in (DOCX, XLSX, PPTX, "text/csv", "application/json"):
        assert media_type in upload
        assert media_type in blob
    assert DOCM not in upload
    assert DOCM not in blob


def test_download_policy_reaches_documents_and_stops_at_macros() -> None:
    policy = constraint("public.publication_asset", "publication_asset_download_policy_check")
    assert DOCX in policy
    assert "support_material" in policy
    assert DOCM not in policy

    authorisation = definition("authorize_publication_asset_download")
    assert DOCX in authorisation
    assert "support_material" in authorisation


def test_only_a_pdf_still_requires_a_raster_manifest() -> None:
    """A document must take the plain finalisation branch. Before this migration
    the branch named MP4 explicitly, so a document would have been sent looking
    for page images it never had."""
    for name in ("finalize_asset_verification", "finalize_asset_verification_v2"):
        body = definition(name)
        assert "expected_media_type<>'application/pdf'" in body
        assert "expected_media_type='video/mp4'" not in body


def test_the_upload_intent_consults_the_shared_contract() -> None:
    body = definition("create_asset_upload_intent")
    assert "asset_media_contract_ok(p_media_type,p_file_name,p_size)" in body
