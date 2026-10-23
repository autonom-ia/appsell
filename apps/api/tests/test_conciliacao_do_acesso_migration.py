"""A 0062 é lida como texto: o que importa é que a leitura continue só leitura,
fora do alcance do runtime, e que o `schema_ready` a conheça (#147).

O SQL rodando de verdade é provado em `test_postgres_checkout_reconciliation.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


def _migration(nome: str, apelido: str) -> ModuleType:
    spec = importlib_util.spec_from_file_location(apelido, VERSOES / nome)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration() -> ModuleType:
    return _migration(next(VERSOES.glob("0062_*.py")).name, "appsell_migration_0062")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_a_leitura_so_le(migration: ModuleType) -> None:
    funcao = next(
        c
        for c in _comandos_de(migration, migration.upgrade)
        if "create function app_private.checkout_reconciliation_report" in c
    )
    assert "language plpgsql stable security definer" in funcao
    for escrita in ("insert into", "update ", "delete from"):
        assert escrita not in funcao.lower()


def test_a_leitura_nao_devolve_dado_pessoal(migration: ModuleType) -> None:
    funcao = migration._FUNCAO
    retorno = funcao[funcao.index("returns table(") : funcao.index(") language")]
    for pessoal in ("email", "identity_sub", "buyer_identity_user_id"):
        assert pessoal not in retorno


def test_o_runtime_nao_recebe_a_funcao(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert f"revoke all on function {migration._RELATORIO} from public" in comandos
    assert not any("grant execute" in c for c in comandos)


def test_o_readiness_aprende_a_funcao_e_a_versao(migration: ModuleType) -> None:
    assert migration._BASES_DO_READY == (
        "app_private.schema_ready_base_0016()",
        "app_private.schema_ready_base_0017()",
    )
    assert migration.revision in migration._READY_ATE_0062
    assert migration._READY_ATE_0062.startswith(migration._READY_ATE_0061[:-2])
    de, para = migration._ready_funcao()
    assert "advance_entitlements_to" in de
    assert para.startswith(de) and migration._RELATORIO in para


def test_a_cabeca_da_cadeia_desce(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[-1] == f"drop function {migration._RELATORIO}"
    # O readiness esquece a função antes de ela sumir.
    de, para = migration._ready_funcao()
    assert any(para in c and de in c for c in comandos[:-1])
