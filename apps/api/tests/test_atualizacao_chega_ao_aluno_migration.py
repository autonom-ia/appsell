"""A 0060 é lida como texto porque é assim que ela age: reescrevendo função.

O que importa aqui não é o SQL rodar (isso os testes de Postgres provam), e sim
que as decisões da migration continuem escritas nela: quais bases do
`schema_ready` aprendem a mudança, onde o avanço se encaixa dentro do
`publish_product`, e que a chave da compra tenha mesmo perdido o
`publication_id`, que era a causa raiz do congelamento (#420).
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1] / "migrations" / "versions" / "0060_atualizacao_chega_ao_aluno.py"
    )
    spec = importlib_util.spec_from_file_location("appsell_migration_0060", path)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _comandos(migration: ModuleType) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        migration.upgrade()
    finally:
        migration.op.execute = original
    return executados


def test_a_chave_da_compra_perde_a_publicacao() -> None:
    """A decisão do Rodrigo, em 04/09/2026, em uma linha de SQL.

    A chave continua garantindo compra, comprador e produto. Ela só deixa de
    exigir que o acesso fique na publicação que está no recibo.
    """
    comandos = _comandos(_migration())
    chave = next(c for c in comandos if "buyer_entitlement_demo_purchase_fkey" in c)

    assert "publication_id" not in chave
    for coluna in ("commerce_purchase_id", "buyer_identity_user_id", "catalog_product_id"):
        assert coluna in chave
    assert any("demo_purchase_acesso_key" in c and "unique" in c for c in comandos), (
        "a chave nova precisa de um alvo unico sem o publication_id do outro lado"
    )


def test_o_readiness_aprende_a_chave_nova_e_a_versao() -> None:
    migration = _migration()

    assert migration._BASES_DO_READY == (
        "app_private.schema_ready_base_0016()",
        "app_private.schema_ready_base_0017()",
    )
    # A `base_0017` chama a `base_0016`, então ensinar a `0016` alcança as duas.
    assert migration._BASE_DO_ACESSO == "app_private.schema_ready_base_0016()"
    assert "demo_purchase_acesso_key" in migration._READY_CHAVE_PARA
    assert "0060_atualizacao_chega_ao_aluno" in migration._READY_ATE_0060


def test_o_avanco_se_encaixa_depois_da_oferta_e_guarda_o_que_o_aluno_comprou() -> None:
    migration = _migration()

    assert migration._AVANCO.startswith(migration._ANCORA), (
        "o avanço precisa entrar logo depois de a oferta apontar para a publicação nova"
    )
    # A guarda é o que sobrou do congelamento: ninguém avança perdendo conteúdo.
    assert "publication_lesson" in migration._AVANCO
    assert "publication_asset" in migration._AVANCO
    # A árvore é refeita, não movida, porque as chaves estrangeiras não são adiáveis.
    for apagada in (
        "delete from public.lesson_progress",
        "delete from public.course_learning_state",
        "delete from public.learning_enrollment",
    ):
        assert apagada in migration._AVANCO
    assert "catalog.entitlements_advanced" in migration._AVANCO


def test_a_migration_e_so_de_ida() -> None:
    migration = _migration()

    try:
        migration.downgrade()
    except RuntimeError as erro:
        assert "forward-only" in str(erro)
    else:  # pragma: no cover - o teste falha antes de chegar aqui
        raise AssertionError("a migration aceitou downgrade")
