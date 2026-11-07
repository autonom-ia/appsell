from __future__ import annotations

import os

import psycopg
import pytest

# 0076 (#561): toda função da 0066 termina o search_path em pg_temp, e continua
# security definer quando era.

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="isolated PostgreSQL environment is required")

ESPERADO = {
    "normalize_email": (False, "search_path=pg_catalog, pg_temp"),
    "email_kind_is_transactional": (False, "search_path=pg_catalog, pg_temp"),
    "email_sha256": (False, "search_path=pg_catalog, app_private, public, pg_temp"),
    "email_optional_block_reason": (True, "search_path=pg_catalog, app_private, public, pg_temp"),
    "record_email_consent": (True, "search_path=pg_catalog, app_private, public, pg_temp"),
    "revoke_email_consent": (True, "search_path=pg_catalog, app_private, public, pg_temp"),
    "unsubscribe_email": (True, "search_path=pg_catalog, app_private, public, pg_temp"),
    "issue_email_unsubscribe_token": (True, "search_path=pg_catalog, app_private, public, pg_temp"),
    "forget_email_consents": (True, "search_path=pg_catalog, app_private, public, pg_temp"),
}


def test_cada_funcao_da_0066_tem_pg_temp_no_fim() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(
            "select p.proname, p.prosecdef, array_to_string(p.proconfig, ',') "
            "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'app_private' and p.proname = any(%s)",
            (list(ESPERADO),),
        ).fetchall()
    assert {nome: (definer, config) for nome, definer, config in linhas} == ESPERADO
