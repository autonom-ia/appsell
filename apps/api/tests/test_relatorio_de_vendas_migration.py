"""A migration do relatório de vendas, lida como texto (#562).

O SQL rodando de verdade está em `test_postgres_relatorio_de_vendas.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest
from cadeia_de_migrations import anterior_na_cadeia

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


def _carregar(sufixo: str, nome: str) -> ModuleType:
    caminho = next(VERSOES.glob(f"*_{sufixo}.py"))
    spec = importlib_util.spec_from_file_location(nome, caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration() -> ModuleType:
    return _carregar("relatorio_de_vendas", "appsell_relatorio_0072")


def _comandos(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_encadeia_na_anterior_e_ensina_o_readiness(migration: ModuleType) -> None:
    # A anterior vem da cadeia (`down_revision`), não da ordem dos arquivos. Ser
    # o topo não é desta migration: quem confere o topo é EXPECTED_MIGRATION.
    assert migration.down_revision == anterior_na_cadeia(migration.revision)
    assert migration.revision in migration.READY_DEPOIS
    assert migration.READY_DEPOIS.startswith(migration.READY_ANTES[:-2])
    assert len(migration.revision) <= 32


def test_nenhuma_tabela_nova_e_so_leitura_e_export_no_runtime(migration: ModuleType) -> None:
    comandos = _comandos(migration, migration.upgrade)
    assert not any("create table" in c for c in comandos)
    concedidas = sorted(c for c in comandos if c.startswith("grant execute"))
    assert concedidas == [
        "grant execute on function app_private.export_sales_rows"
        "(uuid,uuid,date,date,uuid,uuid) to appsell_runtime",
        "grant execute on function app_private.read_sales_report"
        "(uuid,uuid,date,date,uuid) to appsell_runtime",
    ]


def test_as_funcoes_conferem_capacidade_papel_e_periodo(migration: ModuleType) -> None:
    for sql in migration.FUNCOES:
        assert "context_text('capability')" in sql
        assert "context_uuid('identity_user_id') is distinct from p_actor" in sql
        assert "context_uuid('seller_organization_id') is distinct from p_seller" in sql
        assert "('seller_owner','seller_admin')" in sql
        # Até 366 dias de calendário com as duas pontas: a diferença vai até 365.
        assert f"p_to - p_from >= {migration.PERIODO_MAXIMO_DIAS}" in sql
        assert "set search_path=pg_catalog,app_private,public,pg_temp" in sql
        # Nenhum dado pessoal nem payload sai da função.
        for proibido in ("email", "identity_sub", "buyer_identity_user_id", "payload"):
            assert proibido not in sql


def test_o_export_audita_e_limita(migration: ModuleType) -> None:
    export = next(sql for sql in migration.FUNCOES if "export_sales_rows" in sql)
    assert "insert into public.audit_event" in export
    assert "'commerce.sales_exported'" in export
    assert f">= {migration.EXPORTS_POR_MINUTO}" in export
    # A trava do ator vem antes da contagem, para exports simultâneos não
    # lerem a mesma contagem.
    trava = "pg_advisory_xact_lock(hashtextextended('appsell.sales_export:' || p_actor::text, 0))"
    assert trava in export
    assert export.index(trava) < export.index("from public.audit_event")
    assert f"> {migration.MAXIMO_DE_LINHAS}" in export


def test_a_descida_remove_as_funcoes(migration: ModuleType) -> None:
    comandos = _comandos(migration, migration.downgrade)
    assert any("drop function if exists app_private.read_sales_report" in c for c in comandos)
    assert any("drop function if exists app_private.export_sales_rows" in c for c in comandos)
