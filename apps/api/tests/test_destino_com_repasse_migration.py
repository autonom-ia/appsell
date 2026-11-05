"""A migration 0073 do destino com repasse, lida como texto (#545).

O SQL rodando de verdade é provado em `test_postgres_payout_destination_v2.py`.
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
    return _carregar("destino_com_repasse", "appsell_destino_0073")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_a_v2_devolve_os_dois_flags_com_a_trava_da_0024(migration: ModuleType) -> None:
    sql = migration.FUNCAO_DESTINO_V2
    assert "payouts_enabled boolean" in sql
    assert "account.charges_enabled, account.payouts_enabled" in sql
    assert "'commerce.checkout.destination'" in sql
    assert "context_uuid('seller_organization_id') is not null" in sql
    assert "security definer" in sql
    assert "set search_path=pg_catalog,app_private,public,pg_temp" in sql


def test_a_subida_cria_so_a_v2_e_nao_toca_na_0024(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert comandos[0] == "set local role appsell_owner"
    assert comandos[1] == migration.FUNCAO_DESTINO_V2
    assert f"revoke all on function {migration.DESTINO_V2} from public" in comandos
    assert f"grant execute on function {migration.DESTINO_V2} to appsell_runtime" in comandos
    # A imagem anterior da api ainda chama a função da 0024 durante o deploy.
    assert not any("resolve_payout_destination(" in comando for comando in comandos)
    assert not any("drop " in comando for comando in comandos)


def test_a_descida_tira_a_v2_depois_do_readiness(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[0] == "set local role appsell_owner"
    assert comandos[-1] == f"drop function {migration.DESTINO_V2}"


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar("consentimento_de_email", "appsell_consentimento_0066_para_0073")
    assert migration.down_revision == anterior.revision
    assert migration._READY_ATE_0066 == anterior._READY_ATE_0066
    assert migration._READY_ATE_0073.startswith(migration._READY_ATE_0066[:-2])
    assert migration.revision in migration._READY_ATE_0073
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY


def test_a_revisao_cabe_no_alembic_version(migration: ModuleType) -> None:
    assert len(migration.revision) <= 32
