"""A fase 1 de várias ofertas por produto, lida como texto (#553).

O SQL rodando de verdade é provado em `test_postgres_oferta_padrao_do_produto.py`.
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
    return _carregar("oferta_padrao_do_produto", "appsell_oferta_padrao_0067")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_toda_oferta_existente_e_nova_nasce_padrao(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    coluna = next(c for c in comandos if "add column is_default" in c)
    # default true: seeds e o insert legado de update_product_course_offer_draft
    # não informam a coluna e continuam criando a oferta padrão.
    assert "boolean not null default true" in coluna


def test_no_maximo_uma_padrao_por_produto(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    indice = next(c for c in comandos if "offer_one_default_per_product" in c)
    assert "create unique index" in indice
    assert "(seller_organization_id, catalog_product_id)" in indice
    assert indice.rstrip().endswith("where is_default")


def test_a_fase_1_ainda_nao_abre_a_segunda_oferta(migration: ModuleType) -> None:
    for direcao in (migration.upgrade, migration.downgrade):
        comandos = _comandos_de(migration, direcao)
        assert not any("offer_seller_product_key" in c for c in comandos)


def test_a_vitrine_vende_so_a_oferta_padrao(migration: ModuleType) -> None:
    de, para = migration._VITRINE_TROCA
    assert para == de + "   and offer.is_default\n"
    anterior = _carregar("configuracoes_da_loja", "appsell_0052_para_0067")
    # A âncora aparece uma vez na última definição da vitrine (0052).
    assert anterior._VITRINE.count(de) == 1
    assert migration._VITRINE == "app_private.read_public_storefront(text)"


def test_acesso_gratuito_ve_qualquer_oferta_ativa_antes_da_padrao(migration: ModuleType) -> None:
    de, para = migration._ACESSO_TROCA
    # A última definição de grant_public_product_access é a da 0031.
    texto = next(VERSOES.glob("*_public_free_access.py")).read_text()
    assert texto.count(de) == 1
    # Ativa primeiro: um produto com qualquer oferta à venda continua exigindo
    # checkout (P4222), mesmo que a padrão seja rascunho.
    assert para.index("(offer.status='active') desc") < para.index("offer.is_default desc")
    assert para.endswith("offer.version desc,offer.id desc\n")


def test_o_link_de_acesso_nao_muda(migration: ModuleType) -> None:
    # create_product_access_link recusa se QUALQUER oferta do produto estiver
    # ativa. É a escolha conservadora e fica como está.
    comandos = _comandos_de(migration, migration.upgrade)
    assert not any("create_product_access_link" in c for c in comandos)


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar(migration.down_revision.split("_", 1)[1], "appsell_anterior_0067")
    de, para = migration._ready_versoes(anterior)
    assert para == de[:-2] + f",'{migration.revision}'))"
    comandos = _comandos_de(migration, migration.upgrade)
    assert sum(1 for c in comandos if "schema_ready_base" in c) == 2


def test_a_descida_desfaz_na_ordem_inversa_e_nao_apaga_oferta(migration: ModuleType) -> None:
    subida = _comandos_de(migration, migration.upgrade)
    descida = _comandos_de(migration, migration.downgrade)
    assert descida[0] == "set local role appsell_owner"
    funcoes_antes = max(i for i, c in enumerate(descida) if "pg_get_functiondef" in c)
    assert funcoes_antes < descida.index("drop index public.offer_one_default_per_product")
    assert descida[-1] == "alter table public.offer drop column is_default"
    for comandos in (subida, descida):
        assert not any("delete from" in c or "truncate" in c for c in comandos)
