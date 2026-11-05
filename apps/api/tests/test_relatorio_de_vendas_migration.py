"""A migration do relatório de vendas, lida como texto (#562).

O SQL rodando de verdade está em `test_postgres_relatorio_de_vendas.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest

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


def test_encadeia_no_topo_e_ensina_o_readiness(migration: ModuleType) -> None:
    anteriores = sorted(VERSOES.glob("0*.py"))
    nomes = [p.stem for p in anteriores]
    assert nomes[-1] == migration.revision
    assert migration.down_revision == nomes[-2]
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
        assert f"interval '{migration.PERIODO_MAXIMO_DIAS} days'" in sql or (
            f"> {migration.PERIODO_MAXIMO_DIAS}" in sql
        )
        # Nenhum dado pessoal nem payload sai da função.
        for proibido in ("email", "identity_sub", "buyer_identity_user_id", "payload"):
            assert proibido not in sql


def test_o_export_audita_e_limita(migration: ModuleType) -> None:
    export = next(sql for sql in migration.FUNCOES if "export_sales_rows" in sql)
    assert "insert into public.audit_event" in export
    assert "'commerce.sales_exported'" in export
    assert f">= {migration.EXPORTS_POR_MINUTO}" in export
    assert f"> {migration.MAXIMO_DE_LINHAS}" in export


def test_a_descida_remove_as_funcoes(migration: ModuleType) -> None:
    comandos = _comandos(migration, migration.downgrade)
    assert any("drop function if exists app_private.read_sales_report" in c for c in comandos)
    assert any("drop function if exists app_private.export_sales_rows" in c for c in comandos)
