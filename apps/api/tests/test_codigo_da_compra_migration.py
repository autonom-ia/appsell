"""A migration do código da compra, lida como texto (#559, parte B1).

O SQL rodando de verdade é provado em `test_postgres_codigo_da_compra.py`.
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
    return _carregar("codigo_da_compra", "appsell_codigo_da_compra")


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
    assert migration.revision in [caminho.stem for caminho in versoes()]
    assert anterior_na_cadeia(migration.revision) == migration.down_revision
    assert len(migration.revision) <= 32


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar(migration.down_revision.split("_", 1)[1], "appsell_anterior_do_codigo")
    numero = migration.down_revision.split("_", 1)[0]
    assert migration.READY_ANTES == getattr(anterior, f"_READY_ATE_{numero}")
    assert migration._READY_ATE_0070 == migration.READY_ANTES[:-2] + f",'{migration.revision}'))"
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY


def test_o_bloco_do_readiness_entra_no_fim_do_anterior_e_volta(migration: ModuleType) -> None:
    # Pega: as linhas desta entrando no meio do bloco de outra migration, o que
    # faria a próxima abortar com 55000 por casar a âncora 0 ou 2 vezes.
    anterior = _carregar(
        migration._ULTIMA_COM_FUNCAO_NO_READY.split("_", 1)[1], "appsell_anterior_ancora"
    )
    base = "begin\n" + anterior._ready_funcoes()[1] + "  return true;\nend"
    de, para = migration._ready_funcao()
    assert base.count(de) == 1
    texto = base.replace(de, para)
    assert texto.count(anterior._ready_funcoes()[1] + migration._READY_DO_CODIGO) == 1
    assert texto.count(migration._ready_funcoes()[1]) == 1
    assert texto.replace(para, de) == base


def test_o_readiness_confere_tabelas_rls_e_funcoes(migration: ModuleType) -> None:
    bloco = migration._READY_DO_CODIGO
    for tabela in migration.TABELAS:
        assert f"to_regclass('app_private.{tabela}') is not null" in bloco
    assert "relrowsecurity and tabela.relforcerowsecurity) = 2" in bloco
    for assinatura in migration.ASSINATURAS:
        assert f"to_regprocedure('{assinatura}') is not null" in bloco


def test_o_alfabeto_nao_tem_caractere_ambiguo(migration: ModuleType) -> None:
    alfabeto = migration.ALFABETO
    assert len(alfabeto) == len(set(alfabeto)) == 30
    for ambiguo in "0O1ILU":
        assert ambiguo not in alfabeto
    # Sem viés: o limite é o maior múltiplo do alfabeto que cabe num byte.
    assert migration.LIMITE_SEM_VIES == 256 - 256 % len(alfabeto)


def test_a_chave_da_copia_fica_no_prefixo_da_loja(migration: ModuleType) -> None:
    assert migration._CHAVE_DA_COPIA.startswith(
        "'tenants/'||seller_organization_id::text||'/watermarked/'"
    )
    tabela_da_copia = migration._CRIA_TABELAS[1]
    assert "watermarked_asset_copy_tenant_object_key_check" in tabela_da_copia
    assert migration._CHAVE_DA_COPIA in tabela_da_copia


def test_toda_tabela_tem_rls_forcada_e_nenhum_acesso_direto(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    for tabela in migration.TABELAS:
        nome = f"app_private.{tabela}"
        assert f"alter table {nome} enable row level security" in comandos
        assert f"alter table {nome} force row level security" in comandos
        assert f"revoke all on table {nome} from public,appsell_runtime" in comandos
    for assinatura in migration.ASSINATURAS:
        assert f"revoke all on function {assinatura} from public" in comandos
        assert f"grant execute on function {assinatura} to appsell_runtime" in comandos


def test_toda_funcao_e_definer_com_search_path_fixo(migration: ModuleType) -> None:
    for funcao in migration.FUNCOES:
        assert "security definer" in funcao
        assert migration.SEARCH_PATH in funcao


def test_as_funcoes_do_comprador_passam_pela_autorizacao_do_download(
    migration: ModuleType,
) -> None:
    for funcao in (migration._PREPARA, migration._REGISTRA):
        assert "library_context_allowed(p_identity_user_id,'library.asset.download')" in funcao
        assert "app_private.authorize_publication_asset_download(" in funcao
        assert "v_media_type is distinct from 'application/pdf'" in funcao


def test_resolver_exige_capacidade_loja_e_papel(migration: ModuleType) -> None:
    funcao = migration._RESOLVE
    assert f"is distinct from '{migration.CAPACIDADE_RESOLVER}'" in funcao
    assert "context_uuid('seller_organization_id') is distinct from p_seller" in funcao
    assert "not in ('seller_owner','seller_admin')" in funcao
    assert "where codigo.seller_organization_id=p_seller and codigo.code=v_code" in funcao


def test_a_descida_recusa_com_codigo_emitido_antes_de_tudo(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert "purchase codes were issued" in comandos[0]
    assert comandos[-1] == "drop table app_private.purchase_code"
