"""A migration da checagem única de acesso, lida como texto (#558).

O SQL rodando de verdade é provado em `test_postgres_buyer_access_decision.py`.
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
    caminho = next(VERSOES.glob("*_motivo_do_acesso.py"))
    spec = importlib_util.spec_from_file_location("appsell_motivo_do_acesso", caminho)
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


def test_e_a_migration_do_topo(migration: ModuleType) -> None:
    from appsell_api.reset_demo_tenant import EXPECTED_MIGRATION

    # O down_revision acompanha o head da main no merge; só precisa existir.
    assert (VERSOES / f"{migration.down_revision}.py").is_file()
    assert EXPECTED_MIGRATION == migration.revision


def test_contexto_da_biblioteca_antes_de_ler(migration: ModuleType) -> None:
    sql = migration._DECISAO
    assert "security definer" in sql
    assert "set search_path=pg_catalog,app_private,public,pg_temp" in sql
    contexto = sql.index("library_context_allowed(p_identity_user_id,'library.read')")
    assert contexto < sql.index("from buyer_entitlement")
    assert "errcode='28000'" in sql


def test_so_le_o_entitlement_do_proprio_comprador(migration: ModuleType) -> None:
    sql = migration._DECISAO
    assert "entitlement.buyer_identity_user_id=p_identity_user_id" in sql
    # O ledger também é filtrado pela pessoa: o estorno de outro não vaza.
    assert sql.count("grant_row.buyer_identity_user_id=p_identity_user_id") == 2


def test_acesso_so_no_fim_do_caminho_feliz(migration: ModuleType) -> None:
    sql = migration._DECISAO
    liberacoes = [i for i in range(len(sql)) if sql.startswith("select true", i)]
    assert len(liberacoes) == 1
    for negacao in (
        "'not_found'",
        "'refunded'",
        "'revoked'",
        "'expired'",
        "'unavailable'",
        "'not_started'",
        "'seller_unavailable'",
        "'product_unavailable'",
    ):
        assert sql.index(negacao) < liberacoes[0], negacao
    # Status desconhecido nunca vira acesso.
    assert "if v_status is distinct from 'active' then" in sql


def test_nao_emite_motivos_reservados(migration: ModuleType) -> None:
    for reservado in ("'disputed'", "'subscription_lapsed'"):
        assert reservado not in migration._DECISAO


def test_nao_escreve_nada(migration: ModuleType) -> None:
    sql = migration._DECISAO.lower()
    assert "language plpgsql stable" in sql
    for escrita in ("insert ", "update ", "delete ", "perform "):
        assert escrita not in sql


def test_grants_so_para_o_runtime(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    funcao = "app_private.buyer_access_decision(uuid,uuid)"
    assert f"revoke all on function {funcao} from public" in comandos
    assert f"grant execute on function {funcao} to appsell_runtime" in comandos
    assert comandos[0] == "set local role appsell_owner"


def test_ready_conhece_a_funcao_e_a_versao(migration: ModuleType) -> None:
    comandos = "\n".join(_comandos_de(migration, migration.upgrade))
    assert "'0069_motivo_do_acesso'" in comandos
    assert "to_regprocedure('app_private.buyer_access_decision(uuid,uuid)')" in comandos


def test_downgrade_desfaz_na_ordem_inversa(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[0] == "set local role appsell_owner"
    assert comandos[-1] == "drop function app_private.buyer_access_decision(uuid,uuid)"


def _anterior(migration: ModuleType, revisao: str) -> ModuleType:
    caminho = VERSOES / f"{revisao}.py"
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ready_parte_da_lista_da_anterior_na_cadeia(migration: ModuleType) -> None:
    # A lista vem da migration de baixo, não de uma cópia: uma cópia escrita à
    # mão perdia as versões que entraram depois que este PR nasceu.
    numero = migration.down_revision.split("_", 1)[0]
    anterior = getattr(_anterior(migration, migration.down_revision), f"_READY_ATE_{numero}")
    assert migration.READY_ANTES == anterior
    assert migration.READY_DEPOIS == anterior[:-2] + ",'0069_motivo_do_acesso'))"
    assert migration._READY_ATE_0069 == migration.READY_DEPOIS


def test_ancora_das_funcoes_e_a_ultima_que_as_mudou(migration: ModuleType) -> None:
    # A 0068 foi a última a acrescentar função ao ready; a decisão entra depois
    # da linha dela, e não no meio da lista.
    de, para = migration._ready_funcao()
    assert de == _anterior(migration, "0068_segunda_oferta_do_produto")._ready_funcoes()[1]
    linha = "            and to_regprocedure('{}') is not null\n"
    assert para == de + linha.format("app_private.buyer_access_decision(uuid,uuid)")
