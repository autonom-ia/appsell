"""A 0061 e lida como texto porque e assim que ela age: reescrevendo funcao.

O que importa aqui nao e o SQL rodar (isso os testes de Postgres provam), e sim
que as decisoes continuem escritas nela: que o avanco tenha virado **uma**
funcao, que o `publish_product` passe a chamar em vez de carregar o corpo, e que
o backfill chame a mesma funcao em vez de repetir a guarda (#420).
"""

from __future__ import annotations

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
    return _migration("0061_o_atraso_alcanca_o_aluno.py", "appsell_migration_0061")


def _comandos(migration: ModuleType) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        migration.upgrade()
    finally:
        migration.op.execute = original
    return executados


def test_o_avanco_vira_uma_funcao_so(migration: ModuleType) -> None:
    comandos = _comandos(migration)
    funcao = next(c for c in comandos if "create function app_private.advance_entitlements_to" in c)

    # A guarda mora na funcao, e so nela.
    assert "publication_lesson" in funcao
    assert "publication_asset" in funcao
    for apagada in (
        "delete from public.lesson_progress",
        "delete from public.course_learning_state",
        "delete from public.learning_enrollment",
    ):
        assert apagada in funcao
    assert "catalog.entitlements_advanced" in funcao


def test_o_publish_product_passa_a_chamar_em_vez_de_carregar(migration: ModuleType) -> None:
    assert "perform app_private.advance_entitlements_to" in migration._CHAMADA
    assert "'publication'" in migration._CHAMADA
    # O bloco que sai e lido da migration anterior, nao copiado: uma divergencia
    # vira erro do `replace`, e nao um publish_product reescrito pela metade.
    # O nome vem de `down_revision` para nao fixar este teste numa versao.
    anterior = _migration(f"{migration.down_revision}.py", "appsell_migration_anterior")
    assert "avancando" in anterior._AVANCO
    comandos = _comandos(migration)
    assert any(anterior._AVANCO in c for c in comandos), (
        "o bloco da migration anterior nao seria retirado do publish_product"
    )


def test_o_backfill_usa_a_mesma_funcao(migration: ModuleType) -> None:
    """Duas implementacoes da mesma regra divergiriam na primeira mudanca."""
    assert "app_private.advance_entitlements_to" in migration._BACKFILL
    assert "'backfill'" in migration._BACKFILL
    # Nenhuma copia da guarda dentro do backfill.
    assert "publication_lesson" not in migration._BACKFILL
    assert "published_snapshot_id is not null" in migration._BACKFILL


def test_o_readiness_aprende_a_funcao_e_a_versao(migration: ModuleType) -> None:
    assert migration._BASES_DO_READY == (
        "app_private.schema_ready_base_0016()",
        "app_private.schema_ready_base_0017()",
    )
    assert migration._BASE_DO_ACESSO == "app_private.schema_ready_base_0016()"
    assert "advance_entitlements_to" in migration._READY_FUNCAO_PARA
    assert "0061_o_atraso_alcanca_o_aluno" in migration._READY_ATE_0061


def test_a_migration_e_so_de_ida(migration: ModuleType) -> None:
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
