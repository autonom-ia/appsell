"""A 0081, lida como texto.

O SQL rodando de verdade é provado em `test_postgres_curriculum.py`
(`test_criar_depois_de_excluir_nao_esbarra_na_arquivada`).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest
from cadeia_de_migrations import anterior_na_cadeia, versoes

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


@pytest.fixture
def migration() -> ModuleType:
    caminho = next(VERSOES.glob("*_posicao_livre_ao_criar.py"))
    spec = importlib_util.spec_from_file_location("appsell_posicao_0081", caminho)
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


def test_esta_na_cadeia_e_cabe_no_alembic_version(migration: ModuleType) -> None:
    assert migration.revision in [caminho.stem for caminho in versoes()]
    assert anterior_na_cadeia(migration.revision) == migration.down_revision
    assert len(migration.revision) <= 32


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    assert migration.READY_DEPOIS == migration.READY_ANTES[:-2] + f",'{migration.revision}'))"
    assert migration._READY_ATE_0081 == migration.READY_DEPOIS


def test_as_ancoras_existem_uma_vez_no_corpo_original() -> None:
    # Pega: uma âncora que não bate com o corpo da 0005, e a subida falharia
    # em produção com "nao continha a ancora exatamente uma vez".
    fonte = next(VERSOES.glob("0005_*.py")).read_text()
    caminho = next(VERSOES.glob("*_posicao_livre_ao_criar.py"))
    spec = importlib_util.spec_from_file_location("appsell_posicao_0081_ancora", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert fonte.count(module.ANCORA_AULA) == 1
    assert fonte.count(module.ANCORA_MODULO) == 1


def test_so_arquivadas_andam_e_com_constraint_adiada(migration: ModuleType) -> None:
    # As ativas não podem sair de 0..n-1: a reordenação e a leitura dependem disso.
    for desloca, constraint in (
        (migration.DESLOCA_AULA, "lesson_position_key"),
        (migration.DESLOCA_MODULO, "course_module_position_key"),
    ):
        assert "status='archived'" in desloca
        assert "position>=next_position" in desloca
        assert desloca.index(f"set constraints {constraint} deferred") < desloca.index("update ")
        assert desloca.index("update ") < desloca.index(f"set constraints {constraint} immediate")
        assert migration.MARCADOR in desloca


def test_subida_e_descida_sao_idempotentes_pelo_marcador(migration: ModuleType) -> None:
    # O mesmo SQL pode rodar como hotfix antes do release: a subida pula se o
    # marcador já está, e a descida pula se ele não está.
    for sobe in (migration.SOBE_AULA, migration.SOBE_MODULO):
        assert "> 0 then\n    return;" in sobe
        assert migration.MARCADOR in sobe
    for desce in (migration.DESCE_AULA, migration.DESCE_MODULO):
        assert "= 0 then\n    return;" in desce


def test_nao_muda_dono_grant_nem_derruba_funcao(migration: ModuleType) -> None:
    subida = _comandos(migration, migration.upgrade)
    descida = _comandos(migration, migration.downgrade)
    assert subida[0] == descida[0] == "set local lock_timeout = '5s'"
    for direcao in (subida, descida):
        assert not any("owner to" in comando for comando in direcao)
        assert not any(comando.lstrip().startswith("drop ") for comando in direcao)
        assert not any("grant " in comando or "revoke " in comando for comando in direcao)
