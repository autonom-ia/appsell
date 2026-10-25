"""A migration do dono da conta de recebimento, lida como texto (#509).

O SQL rodando de verdade, com o drift criado de propósito, é provado em
`test_postgres_dono_da_conta_payout.py`.
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
    return _carregar("dono_da_conta_payout", "appsell_dono_0065")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_sai_do_papel_so_pelo_tempo_do_alter(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert comandos[:3] == ["reset role", migration.ALINHA_DONO, "set local role appsell_owner"]
    # Depois do alter, nada roda fora do papel do env.py.
    assert "reset role" not in comandos[1:]


def test_a_troca_e_condicional_e_so_da_tabela(migration: ModuleType) -> None:
    sql = migration.ALINHA_DONO
    assert migration.TABELA == "public.seller_payout_account"
    assert sql.index("<> 'appsell_owner'") < sql.index("alter table")
    assert f"alter table {migration.TABELA} owner to appsell_owner" in sql
    assert sql.count("alter ") == 1


def test_a_descida_nao_devolve_o_dono(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[0] == "set local role appsell_owner"
    assert not any("owner to" in comando for comando in comandos)
    assert "reset role" not in comandos


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar("vendas_ativadas_pela_loja", "appsell_ativacao_0064")
    assert migration.down_revision == anterior.revision
    assert migration._READY_ATE_0064 == anterior._READY_ATE_0064
    assert migration._READY_ATE_0065.startswith(migration._READY_ATE_0064[:-2])
    assert migration.revision in migration._READY_ATE_0065
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY


def test_a_revisao_cabe_no_alembic_version(migration: ModuleType) -> None:
    # `alembic_version.version_num` é varchar(32): o primeiro nome desta
    # migration tinha 33 e a cadeia inteira caía no último UPDATE.
    assert len(migration.revision) <= 32
