"""A fase 2 de várias ofertas por produto, lida como texto (#553).

O SQL rodando de verdade é provado em `test_postgres_segunda_oferta.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


def _carregar(sufixo: str, nome: str) -> ModuleType:
    caminho = next(VERSOES.glob(f"*_{sufixo}.py"))
    spec = importlib_util.spec_from_file_location(nome, caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration() -> ModuleType:
    return _carregar("segunda_oferta_do_produto", "appsell_segunda_oferta_0068")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_abre_a_segunda_oferta_sem_mudar_o_default(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert comandos[:3] == [
        "set local role appsell_owner",
        migration._LOCK_TIMEOUT,
        migration._TIRA_UNIQUE,
    ]
    # O default true fica: o insert legado segue criando só a primeira oferta.
    assert not any("is_default" in c and "set default" in c for c in comandos)


def test_so_create_product_offer_grava_oferta_nao_padrao(migration: ModuleType) -> None:
    sql = migration._FUNCAO_CRIA
    assert "'draft', p_unit_amount_minor, p_currency, p_billing_period, false" in sql
    assert "is_default = true" not in sql


def test_funcoes_novas_seguem_o_padrao_de_seguranca(migration: ModuleType) -> None:
    for sql, capability in (
        (migration._FUNCAO_CRIA, "catalog.offer.create"),
        (migration._FUNCAO_PADRAO, "catalog.offer.default.update"),
    ):
        assert f"app_private.context_text('capability') <> '{capability}'" in sql
        assert "context_uuid('seller_organization_id') is distinct from" in sql
        assert "membership.role = 'seller_owner'" in sql
        assert "platform_superadmin_catalog_allowed(\n        p_identity_user_id, " in sql
        assert f"'{capability}'" in sql
        assert "security definer" in sql
        assert "set search_path = pg_catalog, app_private, public, pg_temp" in sql
        assert "insert into public.audit_event" in sql
        assert "pg_advisory_xact_lock" in sql
        # Sem regex: o hash do corpo é conferido por translate.
        assert "~" not in sql


def test_o_produto_e_travado_antes_das_ofertas(migration: ModuleType) -> None:
    cria = migration._FUNCAO_CRIA
    trava = cria.index("from public.catalog_product product")
    assert cria.index("for update", trava) < cria.index("insert into public.offer")
    padrao = migration._FUNCAO_PADRAO
    trava_produto = padrao.index("from public.catalog_product product")
    trava_ofertas = padrao.index("order by offer.id\n  for update")
    assert trava_produto < trava_ofertas < padrao.index("update public.offer")
    # Na transição, a trava do produto entra antes da leitura da oferta.
    trava, leitura = migration._TRANSICAO_TROCAS[0]
    assert leitura.index("from public.catalog_product product") < leitura.index(
        "select * into locked_offer"
    )
    assert trava == migration._ANCORA_DA_TRAVA


def test_rascunho_nunca_vira_padrao_de_produto_a_venda(migration: ModuleType) -> None:
    sql = migration._FUNCAO_PADRAO
    regra = "if current_default.status = 'active' and target.status <> 'active' then"
    assert regra in sql
    # A regra vem antes da troca.
    assert sql.index(regra) < sql.index("set is_default = false")


def test_preco_da_padrao_nao_muda_com_checkout_aberto(migration: ModuleType) -> None:
    sql = migration._FUNCAO_PADRAO
    assert "(target.unit_amount_minor, target.currency, target.billing_period)" in sql
    assert sql.index("price change requires pinned orders") < sql.index("set is_default = false")


def test_troca_da_padrao_em_duas_instrucoes(migration: ModuleType) -> None:
    sql = migration._FUNCAO_PADRAO
    assert sql.index("set is_default = false") < sql.index("set is_default = true")


def test_a_padrao_nao_sai_de_cena_com_outra_oferta(migration: ModuleType) -> None:
    guarda = migration._GUARDA_DA_PADRAO
    assert "locked_offer.is_default and p_offer_status in ('archived', 'draft')" in guarda
    assert "other.status <> 'archived'" in guarda
    assert "P4093" in guarda


def test_ancoras_da_transicao_existem_uma_vez_na_ultima_definicao(migration: ModuleType) -> None:
    fonte = next(VERSOES.glob("*_offer_activation_publication_guard.py")).read_text()
    for de, para in migration._TRANSICAO_TROCAS:
        assert fonte.count(de) == 1
        assert para.endswith(de)
    # A 0032 reescreve a transição, mas não nessas linhas.
    patch_0032 = next(VERSOES.glob("*_platform_superadmin_catalog.py")).read_text()
    for de, _ in migration._TRANSICAO_TROCAS:
        assert de.strip() not in patch_0032


def test_check_de_operacoes_aceita_as_novas(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    check = next(c for c in comandos if "catalog_mutation_request_operation_check" in c)
    assert "'offer.create'" in check and "'offer.default.update'" in check


def test_grants_so_para_o_runtime(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert [c for c in comandos if c.startswith("grant ")] == [
        f"grant execute on function {migration._CRIA} to appsell_runtime",
        f"grant execute on function {migration._PADRAO} to appsell_runtime",
    ]
    assert [c for c in comandos if c.startswith("revoke ")] == [
        f"revoke all on function {migration._CRIA} from public",
        f"revoke all on function {migration._PADRAO} from public",
    ]


def test_o_readiness_aprende_a_versao_e_as_funcoes(migration: ModuleType) -> None:
    de, para = migration._ready_versoes()
    assert de.endswith("'0067_oferta_padrao_do_produto'))")
    assert para == de[:-2] + f",'{migration.revision}'))"
    de_f, para_f = migration._ready_funcoes()
    assert para_f.startswith(de_f)
    assert migration._CRIA in para_f and migration._PADRAO in para_f


def test_a_descida_tem_guarda_e_volta_a_unique(migration: ModuleType) -> None:
    descida = _comandos_de(migration, migration.downgrade)
    assert descida.index(migration._GUARDA_DA_DESCIDA) < descida.index(
        f"drop function {migration._CRIA}"
    )
    assert descida[-1] == migration._VOLTA_UNIQUE
    assert "having count(*) > 1" in migration._GUARDA_DA_DESCIDA
    assert not any("delete from" in c or "truncate" in c for c in descida)
