"""A migration da ativação de vendas, lida como texto (#382).

O SQL rodando de verdade é provado em `test_postgres_financial_activation.py`.
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
    caminho = next(VERSOES.glob("*_vendas_ativadas_pela_loja.py"))
    spec = importlib_util.spec_from_file_location("appsell_ativacao", caminho)
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


def test_vinculo_existente_sai_antes_de_qualquer_escrita(migration: ModuleType) -> None:
    sql = migration._FUNCAO_ATIVA
    retorno = sql.index("return 'already_active'")
    for escrita in ("update public.seller_organization", "insert into public."):
        assert sql.index(escrita) > retorno


def test_recusas_nao_escrevem(migration: ModuleType) -> None:
    sql = migration._FUNCAO_ATIVA
    primeira_escrita = sql.index("update public.seller_organization")
    for recusa in ("seller_inactive", "payout_account_not_ready", "channel_unavailable"):
        assert sql.index(f"return '{recusa}'") < primeira_escrita


def test_o_canal_e_o_da_0059(migration: ModuleType) -> None:
    assert (
        "order by endpoint.created_at,endpoint.seller_organization_id,endpoint.id"
        in migration._FUNCAO_ATIVA
    )
    assert "on conflict do nothing" in migration._FUNCAO_ATIVA


def test_portoes_de_capability_e_loja(migration: ModuleType) -> None:
    assert "is distinct from 'commerce.payout.manage'" in migration._FUNCAO_ATIVA
    assert "is distinct from 'commerce.payout.read'" in migration._FUNCAO_SITUACAO
    for funcao in (migration._FUNCAO_ATIVA, migration._FUNCAO_SITUACAO):
        assert "context_uuid('seller_organization_id') is distinct from p_seller" in funcao
        assert "security definer" in funcao
        assert "set search_path=pg_catalog,app_private,public,pg_temp" in funcao


def test_grants_so_para_o_runtime(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    grants = [c for c in comandos if c.startswith("grant ")]
    assert grants == [
        f"grant execute on function {migration._SITUACAO} to appsell_runtime",
        f"grant execute on function {migration._ATIVA} to appsell_runtime",
    ]


def test_o_readiness_aprende_a_versao_e_as_funcoes(migration: ModuleType) -> None:
    assert migration.revision in migration._READY_ATE_0064
    assert migration._READY_ATE_0064.startswith(migration._READY_ATE_0063[:-2])
    de, para = migration._ready_funcao()
    assert para.startswith(de)
    assert migration._SITUACAO in para and migration._ATIVA in para


def test_a_descida_tira_as_funcoes_e_deixa_os_dados(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[-2:] == [
        f"drop function {migration._ATIVA}",
        f"drop function {migration._SITUACAO}",
    ]
    assert not any("delete from" in c or "truncate" in c for c in comandos)
