"""A migration dos links UTM e da atribuição, lida como texto (#563).

O SQL rodando de verdade está em `test_postgres_links_e_atribuicao.py`.
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
    caminho = next(VERSOES.glob("*_links_e_atribuicao_utm.py"))
    spec = importlib_util.spec_from_file_location("appsell_links_0075", caminho)
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
    nomes = [p.stem for p in sorted(VERSOES.glob("0*.py"))]
    assert nomes[-1] == migration.revision
    assert migration.down_revision == nomes[-2]
    assert migration.revision in migration.READY_DEPOIS
    assert migration.READY_DEPOIS.startswith(migration.READY_ANTES[:-2])
    assert len(migration.revision) <= 32


def test_tabelas_com_rls_forcada_e_sem_acesso_direto(migration: ModuleType) -> None:
    comandos = _comandos(migration, migration.upgrade)
    for tabela in migration.TABELAS:
        nome = f"app_private.{tabela}"
        assert f"alter table {nome} enable row level security" in comandos
        assert f"alter table {nome} force row level security" in comandos
        assert f"revoke all on table {nome} from public,appsell_runtime" in comandos
    criadas = [c for c in comandos if "create table" in c]
    assert len(criadas) == len(migration.TABELAS)
    for sql in criadas:
        assert (
            "seller_organization_id uuid not null references public.seller_organization(id)" in sql
        )
        assert "primary key (seller_organization_id," in sql


def test_nenhuma_tabela_guarda_dado_pessoal(migration: ModuleType) -> None:
    atribuicao = next(c for c in migration.CRIA_TABELAS if "checkout_attribution (" in c)
    contagem = next(c for c in migration.CRIA_TABELAS if "cookie_consent_tally (" in c)
    for sql in (atribuicao, contagem):
        for proibido in ("email", "identity", "ip_", "user_agent", "visitor", "payload"):
            assert proibido not in sql


def test_funcoes_com_pg_temp_e_so_no_runtime(migration: ModuleType) -> None:
    for sql in migration.FUNCOES:
        assert "security definer" in sql
        assert "set search_path=pg_catalog,app_private,public,pg_temp as $$" in sql
    comandos = _comandos(migration, migration.upgrade)
    for assinatura in migration.ASSINATURAS:
        assert f"revoke all on function {assinatura} from public" in comandos
        assert f"grant execute on function {assinatura} to appsell_runtime" in comandos


def test_funcoes_do_painel_conferem_contexto_e_papel(migration: ModuleType) -> None:
    painel = [sql for sql in migration.FUNCOES if "p_actor uuid" in sql]
    assert len(painel) == 4
    for sql in painel:
        assert "context_uuid('identity_user_id') is distinct from p_actor" in sql
        assert "context_uuid('seller_organization_id') is distinct from p_seller" in sql
        assert "('seller_owner','seller_admin')" in sql
    criar = next(sql for sql in painel if "create_campaign_link" in sql)
    assert f"'{migration.CAPACIDADE_ESCRITA}'" in criar
    trava = "pg_advisory_xact_lock(hashtextextended('appsell.campaign_link:' || p_seller::text, 0))"
    assert criar.index(trava) < criar.index("from app_private.campaign_link existing")


def test_atribuicao_confere_a_janela_de_7_dias(migration: ModuleType) -> None:
    gravar = next(sql for sql in migration.FUNCOES if "record_checkout_attribution" in sql)
    assert f"interval '{migration.JANELA_DIAS} days'" in gravar
    assert "on conflict (seller_organization_id,purchase_sha256) do nothing" in gravar


def test_a_descida_remove_funcoes_e_tabelas(migration: ModuleType) -> None:
    comandos = _comandos(migration, migration.downgrade)
    for assinatura in migration.ASSINATURAS:
        assert f"drop function if exists {assinatura}" in comandos
    for tabela in migration.TABELAS:
        assert f"drop table if exists app_private.{tabela}" in comandos
