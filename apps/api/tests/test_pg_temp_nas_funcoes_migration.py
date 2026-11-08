"""A 0079, lida como texto: só troca o search_path das funções antigas (#598)."""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest
from cadeia_de_migrations import anterior_na_cadeia

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"
RUNBOOK = Path(__file__).parents[3] / "docs" / "runbooks" / "release-0079-search-path.md"
VALORES_DE_ANTES = {
    "pg_catalog",
    "pg_catalog, public",
    "pg_catalog, app_private",
    "pg_catalog, app_private, public",
}


@pytest.fixture
def migration() -> ModuleType:
    caminho = next(VERSOES.glob("0079_*.py"))
    spec = importlib_util.spec_from_file_location("appsell_pg_temp_0079", caminho)
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


def test_encadeia_na_cadeia_e_ensina_o_readiness(migration: ModuleType) -> None:
    assert migration.down_revision == anterior_na_cadeia(migration.revision)
    assert migration.READY_DEPOIS == migration.READY_ANTES[:-2] + f",'{migration.revision}'))"
    assert len(migration.revision) <= 32


def test_lista_as_funcoes_medidas_sem_repetir(migration: ModuleType) -> None:
    assinaturas = [assinatura for assinatura, _antes in migration.FUNCOES]
    assert len(assinaturas) == 129
    assert len(set(assinaturas)) == len(assinaturas)
    assert all(assinatura.startswith("app_private.") for assinatura in assinaturas)
    assert {antes for _a, antes in migration.FUNCOES} == VALORES_DE_ANTES


def test_os_valores_de_antes_ja_estao_nas_migrations_que_criaram_as_funcoes(
    migration: ModuleType,
) -> None:
    # A lista não revela nada novo: cada função foi criada, com esse
    # search_path, numa migration que já está no repositório.
    fonte = "\n".join(
        caminho.read_text() for caminho in VERSOES.glob("0*.py") if "0079_" not in caminho.name
    )
    for assinatura, _antes in migration.FUNCOES:
        nome = assinatura.split("(")[0]
        assert f"function {nome}(" in fonte, nome


def test_confere_antes_de_trocar_e_desce_ao_valor_de_antes(migration: ModuleType) -> None:
    subida = [c for c in _comandos(migration, migration.upgrade) if "alter function" in c]
    descida = [c for c in _comandos(migration, migration.downgrade) if "alter function" in c]
    assert len(subida) == len(descida) == 129
    for (assinatura, antes), up, down in zip(migration.FUNCOES, subida, descida, strict=True):
        assert f"'{assinatura}'::regprocedure" in up
        assert f'esperado "{antes}"' in up
        assert f"alter function {assinatura} set search_path = {antes}, pg_temp;" in up
        assert f'esperado "{antes}, pg_temp"' in down
        assert f"alter function {assinatura} set search_path = {antes};" in down


def test_o_abort_diz_a_assinatura_e_o_valor_esperado(migration: ModuleType) -> None:
    sql = migration.confere_e_troca("app_private.f(uuid)", "pg_catalog", "pg_catalog, pg_temp")
    assert "raise exception 'search_path divergiu em app_private.f(uuid): esperado" in sql
    assert 'esperado "pg_catalog", encontrado "%"' in sql
    assert "errcode = '55000'" in sql


def test_so_muda_o_search_path(migration: ModuleType) -> None:
    # Nada de corpo, dono ou grant: fora o readiness, só `alter function ... set`.
    for comando in _comandos(migration, migration.upgrade) + _comandos(
        migration, migration.downgrade
    ):
        if "schema_ready_base_" in comando:
            continue
        for proibido in ("create ", "owner to", "grant ", "revoke ", "drop "):
            assert proibido not in comando.lower(), proibido


def test_o_runbook_confere_a_mesma_lista_da_migration(migration: ModuleType) -> None:
    # A consulta só de leitura que a Trilha 1 roda em produção antes do deploy
    # (#598) precisa listar exatamente as funções e os valores da migration.
    texto = RUNBOOK.read_text()
    linhas = [linha.strip().rstrip(",") for linha in texto.splitlines()]
    pares = [linha for linha in linhas if linha.startswith("('app_private.")]
    esperado = [f"('{assinatura}', '{antes}')" for assinatura, antes in migration.FUNCOES]
    assert pares == esperado
