"""A migration do mapping do produto novo, lida como texto (#465).

O SQL rodando de verdade é provado em `test_postgres_financial_product_mapping.py`.
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
    return _migration(next(VERSOES.glob("*_mapping_do_produto_novo.py")).name, "appsell_mapping")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_so_acrescenta(migration: ModuleType) -> None:
    for sql in (migration._FUNCAO_MAPEIA, migration._BACKFILL):
        assert "on conflict do nothing" in sql
        for mudanca in ("update ", "delete from", "truncate"):
            assert mudanca not in sql.lower()


def test_so_loja_vinculada_e_com_o_canal(migration: ModuleType) -> None:
    for sql in (migration._FUNCAO_MAPEIA, migration._BACKFILL):
        assert "financial_operator_id is not null" in sql
        assert "financial_product_catalog_item_id is not null" in sql
        assert "app_private.financial_channel_integration(" in sql


def test_mapeia_como_a_0059(migration: ModuleType) -> None:
    hash_do_produto = "encode(digest(convert_to(product.id::text,'UTF8'),'sha256'),'hex')"
    assert hash_do_produto in migration._BACKFILL
    assert hash_do_produto.replace("product.", "new.") in migration._FUNCAO_MAPEIA


def test_o_canal_e_a_identidade_que_a_0059_grava(migration: ModuleType) -> None:
    canal = _migration("0059_canal_financial_global.py", "appsell_migration_0059")
    upgrade_0059 = "\n".join(_comandos_de(canal, canal.upgrade))
    assert "'generic_hmac_v1','Financial checkout','active'" in upgrade_0059
    sql = migration._FUNCAO_CANAL_GLOBAL
    assert "integration.adapter='generic_hmac_v1'" in sql
    assert "integration.display_name='Financial checkout'" in sql
    assert "if identities <> 1 then" in sql
    assert "errcode='55000'" in sql
    # A escolha não depende de idade: quem decide é a identidade única.
    assert sql.index("if identities <> 1") < sql.index("order by")
    assert "app_private.financial_channel()" in migration._FUNCAO_CANAL


def test_funcoes_fechadas(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert f"revoke all on function {migration._CANAL_GLOBAL} from public" in comandos
    assert f"revoke all on function {migration._CANAL} from public" in comandos
    assert f"revoke all on function {migration._MAPEIA} from public" in comandos
    assert not any("grant " in c for c in comandos)
    for funcao in (
        migration._FUNCAO_CANAL_GLOBAL,
        migration._FUNCAO_CANAL,
        migration._FUNCAO_MAPEIA,
    ):
        assert "security definer" in funcao
        assert "set search_path=pg_catalog,app_private,public,pg_temp" in funcao


def test_o_readiness_aprende_a_versao_e_o_trigger(migration: ModuleType) -> None:
    assert migration.revision in migration._READY_ATE_0062
    assert migration._READY_ATE_0062.startswith(migration._READY_ATE_0061[:-2])
    de, para = migration._ready_funcao()
    assert para.startswith(de)
    for objeto in (migration._CANAL_GLOBAL, migration._CANAL, migration._TRIGGER):
        assert objeto in para


def test_a_descida_tira_o_trigger_e_deixa_os_mappings(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[-4:] == [
        f"drop trigger {migration._TRIGGER} on public.catalog_product",
        f"drop function {migration._MAPEIA}",
        f"drop function {migration._CANAL}",
        f"drop function {migration._CANAL_GLOBAL}",
    ]
    assert not any("external_product_mapping" in c for c in comandos)
