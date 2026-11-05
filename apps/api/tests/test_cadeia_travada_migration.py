"""A 0071 é lida como texto: a leitura só lê, só conta, fica fora do runtime e
o `schema_ready` a conhece (#550).

O SQL rodando de verdade é provado em `test_postgres_cadeia_travada.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


@pytest.fixture
def migration() -> ModuleType:
    caminho = next(VERSOES.glob("*_cadeia_travada.py"))
    spec = importlib_util.spec_from_file_location("appsell_cadeia_travada", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        if "create function app_private.chain_health_report" in c
    )
    assert "language plpgsql stable security definer" in funcao
    for escrita in ("insert into", "update ", "delete from"):
        assert escrita not in funcao.lower()


def test_a_leitura_so_devolve_metrica_quantidade_e_instante(migration: ModuleType) -> None:
    funcao = migration._FUNCAO
    retorno = funcao[funcao.index("returns table(") : funcao.index(")\nlanguage")]
    assert retorno == "returns table(metric text, quantity bigint, observed_at timestamptz"
    # Nenhuma coluna de pessoa ou de identificador: se uma entrar, o teste cai.
    colunas = [c.strip().split(" ")[0] for c in retorno.removeprefix("returns table(").split(",")]
    for coluna in colunas:
        assert not coluna.endswith("_id"), coluna
        for pessoal in ("email", "slug", "sha256", "sub", "name", "buyer", "recipient"):
            assert pessoal not in coluna, coluna
    corpo = funcao.lower()
    # Nenhum campo de pessoa sai: só agregados.
    for pessoal in ("recipient_email", "email_normalized", "identity_sub", "payload"):
        assert pessoal not in corpo


def test_o_runtime_nao_recebe_a_leitura(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert f"revoke all on function {migration._LEITURA} from public" in comandos
    concessoes = [c for c in comandos if "grant " in c and " to " in c]
    assert concessoes == [migration._CONCEDE]
    assert "appsell_runtime" not in migration._CONCEDE
    assert "if exists(select 1 from pg_roles where rolname='appsell_reconciler')" in (
        migration._CONCEDE
    )


def test_a_funcao_fecha_o_search_path_e_usa_o_instante_da_transacao(
    migration: ModuleType,
) -> None:
    funcao = migration._FUNCAO
    assert "set search_path=pg_catalog,app_private,public,pg_temp" in funcao
    assert "clock_timestamp()" not in funcao


def test_o_ready_conhece_a_versao_e_a_leitura(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    reescritas = [c for c in comandos if "pg_get_functiondef" in c]
    assert len(reescritas) == 3
    assert all("'0071_cadeia_travada'" in c for c in reescritas[:2])
    assert f"to_regprocedure('{migration._LEITURA}') is not null" in reescritas[2]


def test_o_downgrade_desfaz_na_ordem_inversa(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[0] == "set local role appsell_owner"
    assert comandos[-2] == migration._RETIRA
    assert comandos[-1] == f"drop function {migration._LEITURA}"


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    caminho = next(VERSOES.glob("*_dono_da_conta_payout.py"))
    spec = importlib_util.spec_from_file_location("appsell_dono_0065_para_0071", caminho)
    assert spec is not None and spec.loader is not None
    anterior = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(anterior)
    assert migration.down_revision == anterior.revision
    assert migration._READY_ATE_0065 == anterior._READY_ATE_0065
    assert migration._READY_ATE_0071.startswith(migration._READY_ATE_0065[:-2])
    assert migration.revision in migration._READY_ATE_0071
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY
    # `alembic_version.version_num` é varchar(32).
    assert len(migration.revision) <= 32
