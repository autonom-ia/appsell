"""A migration 0074 do acesso em disputa, lida como texto (#542).

O SQL rodando de verdade é provado em `test_postgres_acesso_em_disputa.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest

from appsell_api.reset_demo_tenant import EXPECTED_MIGRATION

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
    return _carregar("acesso_em_disputa", "appsell_disputa_0074")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def _ancora_da_0020(trecho: str) -> int:
    fonte = (VERSOES / "0020_checkout_fulfillment.py").read_text(encoding="utf-8")
    return fonte.count(trecho)


def test_as_ancoras_existem_uma_vez_na_funcao_de_fulfillment(migration: ModuleType) -> None:
    # Pega: âncora digitada errada, que só falharia no deploy.
    for de, _para in migration._TROCAS_NA_APLICACAO:
        assert _ancora_da_0020(de) == 1, de


def test_a_0049_nao_tocou_nas_ancoras(migration: ModuleType) -> None:
    # Pega: a inserção da 0049 cobrindo um trecho que esta migration procura.
    fonte = (VERSOES / "0049_caixa_de_saida_de_email.py").read_text(encoding="utf-8")
    for de, _para in migration._TROCAS_NA_APLICACAO:
        assert de not in fonte


def test_o_desvio_entra_antes_do_ramo_de_estorno(migration: ModuleType) -> None:
    # Pega: a disputa caindo no ramo de estorno e virando "refunded".
    assert migration._DESVIO_PARA.endswith(migration._DESVIO_DE)
    assert "app_private.apply_checkout_dispute(" in migration._DESVIO_PARA


def test_as_ancoras_da_conciliacao_existem_uma_vez(migration: ModuleType) -> None:
    # Pega: âncora da conciliação digitada errada, que só falharia no deploy.
    fonte = (VERSOES / "0063_conciliacao_do_acesso.py").read_text(encoding="utf-8")
    for de, _para in migration._TROCAS_NA_CONCILIACAO:
        assert fonte.count(de) == 1, de


def test_a_conciliacao_ganha_as_categorias_da_disputa(migration: ModuleType) -> None:
    trocas = "".join(para for _de, para in migration._TROCAS_NA_CONCILIACAO)
    for categoria in (
        "'disputed_with_access'",
        "'suspended_without_dispute'",
        "'dispute_without_fulfillment'",
        "'dispute_without_created_at'",
        "'disputed_while_disabled'",
    ):
        assert categoria in trocas


def test_o_estorno_nao_troca_a_perdida(migration: ModuleType) -> None:
    assert "then 'charged_back' else 'refunded' end" in migration._ESTORNO_GRANT_PARA


def test_aprovacao_repetida_nao_reativa_disputa(migration: ModuleType) -> None:
    assert "in ('refunded','suspended','charged_back')" in migration._APROVACAO_PARA


def test_estorno_revoga_tambem_o_suspenso(migration: ModuleType) -> None:
    assert "buyer_entitlement.status in ('active','suspended')" in migration._ESTORNO_PARA
    assert "then 'suspended' else 'revoked' end" in migration._ESTORNO_PARA


def test_estados_novos_sem_tirar_os_antigos(migration: ModuleType) -> None:
    assert migration._GRANT_AGORA.startswith(migration._GRANT_ANTES + ",")
    assert migration._ENTITLEMENT_AGORA.startswith(migration._ENTITLEMENT_ANTES + ",")
    assert migration._TIPOS_DE_AGORA.startswith(migration._TIPOS_DE_ANTES + ",")
    assert "'charged_back'" in migration._GRANT_AGORA
    assert "'suspended'" in migration._ENTITLEMENT_AGORA


def test_a_funcao_de_disputa_e_fechada(migration: ModuleType) -> None:
    sql = migration._FUNCAO_DISPUTA
    assert "security definer" in sql
    assert "set search_path=pg_catalog,app_private,public,pg_temp" in sql
    assert "set search_path=pg_catalog,app_private,public,pg_temp" in migration._ACEITA
    assert "'commerce.fulfillment.worker'" in sql
    assert "app_private.dispute_access_switch" in sql
    comandos = _comandos_de(migration, migration.upgrade)
    assert f"revoke all on function {migration._APPLY_DISPUTA} from public" in comandos
    assert not any(
        "grant execute" in comando and "apply_checkout_dispute" in comando for comando in comandos
    )


def test_tabelas_novas_tem_rls_forcada_e_ficam_fora_do_runtime(migration: ModuleType) -> None:
    # Pega: tabela nova sem RLS enable + force (§7) ou legível pela api.
    comandos = _comandos_de(migration, migration.upgrade)
    for tabela in migration._TABELAS_NOVAS:
        assert f"alter table {tabela} enable row level security" in comandos
        assert f"alter table {tabela} force row level security" in comandos
        assert f"revoke all on table {tabela} from public,appsell_runtime" in comandos
    assert f"grant execute on function {migration._ACEITA_DISPUTA} to appsell_runtime" in comandos


def test_o_readiness_confere_as_tabelas_e_funcoes_novas(migration: ModuleType) -> None:
    de, para = migration._ready_funcao()
    assert para.startswith(de)
    for tabela in migration._TABELAS_NOVAS:
        assert f"to_regclass('{tabela}') is not null" in para
    assert "relforcerowsecurity" in para
    assert migration._APPLY_DISPUTA in para and migration._ACEITA_DISPUTA in para


def test_a_chave_por_loja_nasce_vazia_e_fora_do_runtime(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert migration._CHAVE_POR_LOJA in comandos
    assert not any("insert into app_private.dispute_access_switch" in c for c in comandos)
    assert migration._GATILHO_SO_INSERCAO in comandos
    assert (
        "revoke all on table app_private.dispute_access_switch from public,appsell_runtime"
        in comandos
    )


def test_o_esquema_muda_como_dono_e_volta_ao_papel(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    assert comandos[0] == "reset role"
    assert "set local role appsell_owner" in comandos
    assert comandos.index("set local role appsell_owner") < comandos.index(
        migration._FUNCAO_DISPUTA
    )


def test_a_descida_recusa_com_disputa_registrada(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert comandos[0] == "set local role appsell_owner"
    assert "ha disputa ou chave registrada" in comandos[1]
    assert comandos[-1] == "set local role appsell_owner"


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar("consentimento_de_email", "appsell_consentimento_0066_para_0074")
    assert migration.down_revision == anterior.revision
    assert migration._READY_ATE_0066 == anterior._READY_ATE_0066
    assert migration._READY_ATE_0074.startswith(migration._READY_ATE_0066[:-2])
    assert migration.revision in migration._READY_ATE_0074
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY
    assert EXPECTED_MIGRATION == migration.revision


def test_a_revisao_cabe_no_alembic_version(migration: ModuleType) -> None:
    assert len(migration.revision) <= 32
