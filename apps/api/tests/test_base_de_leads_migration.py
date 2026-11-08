"""Migration 0078: base de leads da loja (#619).

Confere o texto da migration sem banco: a cadeia, o readiness, a guarda, o
export só de marketing e a descida. O comportamento contra o Postgres real
está em `test_postgres_base_de_leads.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest
from cadeia_de_migrations import anterior_na_cadeia, versoes

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


def _carregar() -> ModuleType:
    caminho = VERSOES / "0078_base_de_leads.py"
    spec = importlib_util.spec_from_file_location("appsell_base_de_leads_0078", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration() -> ModuleType:
    return _carregar()


def _comandos(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_encadeia_pela_cadeia_e_ensina_o_readiness(migration: ModuleType) -> None:
    assert migration.revision in {caminho.stem for caminho in versoes()}
    assert migration.down_revision == anterior_na_cadeia(migration.revision)
    assert migration.READY_DEPOIS == migration.READY_ANTES[:-2] + f",'{migration.revision}'))"
    assert f"'{migration.down_revision}'" in migration.READY_ANTES
    assert migration._READY_ATE_0078 == migration.READY_DEPOIS
    assert len(migration.revision) <= 32


def test_nenhuma_tabela_nova_e_so_as_duas_funcoes_no_runtime(migration: ModuleType) -> None:
    comandos = _comandos(migration, migration.upgrade)
    assert not any("create table" in c for c in comandos)
    concedidas = sorted(c for c in comandos if c.startswith("grant execute"))
    assert concedidas == [
        "grant execute on function app_private.export_store_leads"
        "(uuid,uuid,date,date,uuid,uuid) to appsell_runtime",
        "grant execute on function app_private.read_store_leads"
        "(uuid,uuid,date,date,uuid) to appsell_runtime",
    ]


def test_nenhum_marcador_sobra_no_sql(migration: ModuleType) -> None:
    for sql in migration.FUNCOES:
        assert "__" not in sql


def test_as_funcoes_conferem_capacidade_papel_e_periodo(migration: ModuleType) -> None:
    capacidades = (migration.CAPACIDADE_LEITURA, migration.CAPACIDADE_EXPORT)
    for sql, capacidade in zip(migration.FUNCOES, capacidades, strict=True):
        assert f"context_text('capability') is distinct from '{capacidade}'" in sql
        assert "context_uuid('identity_user_id') is distinct from p_actor" in sql
        assert "context_uuid('seller_organization_id') is distinct from p_seller" in sql
        assert "('seller_owner','seller_admin')" in sql
        assert f"p_to - p_from >= {migration.PERIODO_MAXIMO_DIAS}" in sql
        assert "set search_path=pg_catalog,app_private,public,pg_temp" in sql
        assert "ent.seller_organization_id=p_seller" in sql


def test_demo_e_pedido_de_exclusao_aberto_ficam_de_fora(migration: ModuleType) -> None:
    for sql in migration.FUNCOES:
        assert "ent.source in ('billing','free_access','manual')" in sql
        assert "'account.deletion.requested'" in sql
        assert "'account.deletion.completed'" in sql


def test_o_export_e_so_marketing_e_usa_a_regra_do_envio(migration: ModuleType) -> None:
    export = next(sql for sql in migration.FUNCOES if "export_store_leads" in sql)
    assert "where situacao_do_email='granted'" in export
    assert "app_private.email_optional_block_reason(p_seller,acessos.email,'store_news')" in export


def test_o_export_audita_sem_email_e_limita(migration: ModuleType) -> None:
    export = next(sql for sql in migration.FUNCOES if "export_store_leads" in sql)
    assert "'growth.leads_exported'" in export
    auditoria = export[export.index("insert into public.audit_event") :]
    assert "email" not in auditoria.split("return")[0]
    assert f">= {migration.EXPORTS_POR_MINUTO}" in export
    trava = "pg_advisory_xact_lock(hashtextextended('appsell.leads_export:' || p_actor::text, 0))"
    assert trava in export
    assert export.index(trava) < export.index("from public.audit_event audit")
    assert f"> {migration.MAXIMO_DE_LINHAS}" in export


def test_a_descida_remove_as_funcoes_e_devolve_o_readiness(migration: ModuleType) -> None:
    comandos = _comandos(migration, migration.downgrade)
    assert any("drop function if exists app_private.read_store_leads" in c for c in comandos)
    assert any("drop function if exists app_private.export_store_leads" in c for c in comandos)
    reescritas = [c for c in comandos if "pg_get_functiondef" in c]
    assert len(reescritas) == len(migration._BASES_DO_READY)
    assert all(migration.READY_DEPOIS in c for c in reescritas)
