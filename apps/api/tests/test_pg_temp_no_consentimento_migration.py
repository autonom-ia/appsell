"""A 0076, lida como texto: só troca o search_path das funções da 0066."""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


@pytest.fixture
def migration() -> ModuleType:
    caminho = next(VERSOES.glob("*_pg_temp_no_consentimento.py"))
    spec = importlib_util.spec_from_file_location("appsell_pg_temp_0076", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    # Encadeia na anterior da pasta; uma migration nova depois dela não quebra o teste.
    nomes = [p.stem for p in sorted(VERSOES.glob("0*.py"))]
    posicao = nomes.index(migration.revision)
    assert migration.down_revision == nomes[posicao - 1]
    assert migration.READY_DEPOIS.startswith(migration.READY_ANTES[:-2])
    assert len(migration.revision) <= 32


def test_cobre_toda_funcao_da_0066_com_search_path(migration: ModuleType) -> None:
    fonte = next(VERSOES.glob("*_consentimento_de_email.py")).read_text()
    criadas = {trecho.split("(")[0] for trecho in fonte.split("create function ")[1:]}
    alteradas = {assinatura.split("(")[0] for assinatura, _a, _d in migration.FUNCOES}
    assert alteradas == criadas
    assert len(migration.FUNCOES) == 9


def test_sobe_com_pg_temp_no_fim_e_desce_ao_valor_de_antes(migration: ModuleType) -> None:
    subida = [c for c in _comandos(migration, migration.upgrade) if c.startswith("alter function")]
    descida = [
        c for c in _comandos(migration, migration.downgrade) if c.startswith("alter function")
    ]
    assert len(subida) == len(descida) == 9
    assert all(c.endswith(", pg_temp") for c in subida)
    assert not any("pg_temp" in c for c in descida)
    # Só o search_path muda: nada de corpo, dono ou grant.
    for comando in subida + descida:
        alvo, separador, valor = comando.partition(" set search_path = ")
        assert separador and alvo.startswith("alter function app_private.")
        assert " " not in alvo.removeprefix("alter function ")
        assert valor in {
            "pg_catalog",
            "pg_catalog, pg_temp",
            "pg_catalog, app_private, public",
            "pg_catalog, app_private, public, pg_temp",
        }
