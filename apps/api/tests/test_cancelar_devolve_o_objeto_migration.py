"""A 0080, lida como texto (#642).

O SQL rodando de verdade é provado em `test_postgres_private_assets.py`.
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
    caminho = next(VERSOES.glob("*_cancelar_devolve_o_objeto.py"))
    spec = importlib_util.spec_from_file_location("appsell_cancelar_0080", caminho)
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
    # "Está na cadeia", e não "é o topo": outra migration pode entrar depois.
    assert migration.revision in [caminho.stem for caminho in versoes()]
    assert anterior_na_cadeia(migration.revision) == migration.down_revision
    assert len(migration.revision) <= 32


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    assert migration.READY_DEPOIS == migration.READY_ANTES[:-2] + f",'{migration.revision}'))"
    assert migration._READY_ATE_0080 == migration.READY_DEPOIS


def test_a_descida_devolve_o_corpo_exato_da_0008(migration: ModuleType) -> None:
    # Pega: um corpo de descida reescrito à mão, que divergiria do original
    # sem ninguém perceber até um rollback.
    fonte = next(VERSOES.glob("0008_*.py")).read_text()
    assert fonte.count(migration.CANCELAR_ANTES) == 1


def test_a_subida_so_acrescenta_o_storage(migration: ModuleType) -> None:
    # O corpo novo é o antigo com três mudanças, e nenhuma outra: a regra de
    # quem pode cancelar, o registro de idempotência e o audit continuam iguais.
    antes = migration.CANCELAR_ANTES.replace("create function", "create or replace function", 1)
    depois = migration.CANCELAR_DEPOIS
    repeticao = "jsonb_set(replay,'{cancelled}','false'::jsonb,true)"
    esperado = (
        antes.replace(migration.SEARCH_PATH_DA_0008, migration.SEARCH_PATH, 1)
        .replace(
            "replay jsonb; result jsonb;\n",
            "replay jsonb; result jsonb;\n          storage jsonb;\n",
            1,
        )
        .replace(
            "'catalog.asset.upload.cancel');\n",
            "'catalog.asset.upload.cancel');\n"
            "          storage:=jsonb_build_object('storage_driver',candidate.storage_driver,\n"
            "            'bucket',candidate.quarantine_bucket,"
            "'object_key',candidate.quarantine_key,\n"
            "            'byte_size',candidate.expected_byte_size);\n",
            1,
        )
        .replace(
            f"if replay is not null then return {repeticao}; end if;",
            "if replay is not null then\n"
            f"            return {repeticao}\n"
            "              || jsonb_build_object('storage',storage);\n"
            "          end if;",
            1,
        )
        .replace(
            "          return result;\n",
            "          return result || jsonb_build_object('storage',storage);\n",
            1,
        )
    )
    assert depois == esperado


def test_o_storage_nao_entra_no_registro_de_idempotencia(migration: ModuleType) -> None:
    # O registro guarda `result` antes do `||`: o retorno gravado não muda, e
    # uma repetição de um cancelamento antigo continua igual ao que era.
    corpo = migration.CANCELAR_DEPOIS
    assert "candidate.asset_id,p_upload,result);" in corpo
    assert corpo.index("record_asset_mutation") < corpo.index("return result || ")


def test_continua_definer_com_search_path_fixo_e_os_mesmos_grants(migration: ModuleType) -> None:
    subida = _comandos(migration, migration.upgrade)
    descida = _comandos(migration, migration.downgrade)
    assert subida[0] == descida[0] == "set local lock_timeout = '5s'"
    assert "security definer" in migration.CANCELAR_DEPOIS
    assert migration.SEARCH_PATH in migration.CANCELAR_DEPOIS
    for direcao in (subida, descida):
        assert f"revoke all on function {migration.ASSINATURA} from public" in direcao
        assert f"grant execute on function {migration.ASSINATURA} to appsell_runtime" in direcao
        assert not any("owner to" in comando for comando in direcao)
        assert not any(comando.lstrip().startswith("drop ") for comando in direcao)
