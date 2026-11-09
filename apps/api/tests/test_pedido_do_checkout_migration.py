"""A migration do pedido do checkout, lida como texto (#154, fatia 1).

O SQL rodando de verdade é provado em `test_postgres_pedido_do_checkout.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest
from cadeia_de_migrations import anterior_na_cadeia, versoes

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
    return _carregar("pedido_do_checkout", "appsell_pedido_do_checkout")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_esta_na_cadeia_e_cabe_no_alembic_version(migration: ModuleType) -> None:
    assert migration.revision.startswith("0077_")
    assert migration.revision in [caminho.stem for caminho in versoes()]
    assert anterior_na_cadeia(migration.revision) == migration.down_revision
    assert len(migration.revision) <= 32


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar(migration.down_revision.split("_", 1)[1], "appsell_anterior_do_pedido")
    numero = migration.down_revision.split("_", 1)[0]
    assert migration.READY_ANTES == getattr(anterior, f"_READY_ATE_{numero}")
    assert migration._READY_ATE_0077 == migration.READY_ANTES[:-2] + f",'{migration.revision}'))"
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY


def test_o_bloco_do_readiness_entra_no_fim_do_anterior_e_volta(migration: ModuleType) -> None:
    # Pega: as linhas desta entrando no meio do bloco de outra migration, o que
    # faria a próxima abortar com 55000 por casar a âncora 0 ou 2 vezes.
    anterior = _carregar(
        migration._ULTIMA_COM_FUNCAO_NO_READY.split("_", 1)[1], "appsell_anterior_ancora_pedido"
    )
    base = "begin\n" + anterior._ready_funcoes()[1] + "  return true;\nend"
    de, para = migration._ready_funcao()
    assert base.count(de) == 1
    texto = base.replace(de, para)
    assert texto.count(anterior._ready_funcoes()[1] + migration._READY_DO_PEDIDO) == 1
    assert texto.count(migration._ready_funcoes()[1]) == 1
    assert texto.replace(para, de) == base


def test_o_readiness_confere_tabela_rls_e_funcoes(migration: ModuleType) -> None:
    bloco = migration._READY_DO_PEDIDO
    assert "to_regclass('app_private.checkout_order') is not null" in bloco
    assert "tabela.relrowsecurity and tabela.relforcerowsecurity" in bloco
    for assinatura in migration.ASSINATURAS:
        assert f"to_regprocedure('{assinatura}') is not null" in bloco


def test_a_tabela_e_da_loja_e_tem_os_nomes_combinados(migration: ModuleType) -> None:
    # Pega: um nome trocado. A conciliação (#147) e a Dinheiro leem estes campos.
    tabela = migration._CRIA_TABELA
    assert "primary key (seller_organization_id,id)" in tabela
    assert "unique (seller_organization_id,purchase_sha256)" in tabela
    for coluna in (
        "purchase_sha256",
        "product_id",
        "offer_id",
        "offer_version",
        "coupon_id",
        "currency",
        "billing_period",
        "unit_amount_minor",
        "discount_minor",
        "charged_minor",
        "platform_fee_minor",
        "seller_net_minor",
        "plan_key",
        "buyer_sha256",
        "status",
        "created_at",
        "expires_at",
    ):
        assert f"\n      {coluna} " in tabela, coluna


def test_os_checks_sao_de_consistencia_e_nao_de_preco_minimo(migration: ModuleType) -> None:
    tabela = migration._CRIA_TABELA
    assert "charged_minor = unit_amount_minor - discount_minor" in tabela
    assert "seller_net_minor = charged_minor - platform_fee_minor" in tabela
    assert "(platform_fee_minor is null) = (seller_net_minor is null)" in tabela
    # O mínimo de cobrança mora só em lib/minimum-price.ts (#546).
    assert ">= 50" not in tabela
    assert "charged_minor > 0" in tabela


def test_toda_tabela_tem_rls_forcada_e_nenhum_acesso_direto(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    nome = "app_private.checkout_order"
    assert f"alter table {nome} enable row level security" in comandos
    assert f"alter table {nome} force row level security" in comandos
    assert f"revoke all on table {nome} from public,appsell_runtime" in comandos
    for assinatura in migration.ASSINATURAS:
        assert f"alter function {assinatura} owner to appsell_owner" in comandos
        assert f"revoke all on function {assinatura} from public" in comandos
        assert f"grant execute on function {assinatura} to appsell_runtime" in comandos


def test_toda_funcao_e_definer_com_search_path_fixo(migration: ModuleType) -> None:
    for funcao in migration.FUNCOES:
        assert "security definer" in funcao
        assert migration.SEARCH_PATH in funcao


def test_as_funcoes_exigem_o_worker_do_checkout_sem_loja_no_contexto(
    migration: ModuleType,
) -> None:
    # Pega: um envelope de usuário (com loja) chegando à gravação do pedido.
    for funcao in migration.FUNCOES:
        assert f"is distinct from '{migration.CAPACIDADE}'" in funcao
        assert "app_private.context_uuid('seller_organization_id') is not null" in funcao


def test_criar_le_a_oferta_padrao_a_venda_sob_trava(migration: ModuleType) -> None:
    funcao = migration._CRIA_PEDIDO
    assert "seller.status='active'" in funcao
    assert "product.status='published'" in funcao
    assert "product.published_snapshot_id=p_publication_id" in funcao
    assert "offer.is_default" in funcao
    assert "offer.status='active'" in funcao
    assert "for share of offer" in funcao
    assert "offer.unit_amount_minor is distinct from p_unit_amount_minor" in funcao
    assert f"errcode='{migration.PRECO_MUDOU}'" in funcao


def test_a_descida_recusa_com_pedido_gravado_antes_de_tudo(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert "checkout orders were recorded" in comandos[0]
    assert comandos[-1] == "drop table app_private.checkout_order"


def test_o_ttl_do_pedido_e_o_da_sessao_do_checkout(migration: ModuleType) -> None:
    rota = Path(__file__).parents[3] / "app/api/checkout/stripe/create-session/route.ts"
    assert "const CHECKOUT_SESSION_TTL_SECONDS = 31 * 60;" in rota.read_text()
    assert migration.VALIDADE_EM_SEGUNDOS == 31 * 60
