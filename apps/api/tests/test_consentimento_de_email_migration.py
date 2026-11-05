"""A migration do consentimento de e-mail, lida como texto (#561).

O SQL rodando de verdade é provado em `test_postgres_consentimento_de_email.py`.
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
    return _carregar("consentimento_de_email", "appsell_consentimento_0066")


def _comandos_de(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar("dono_da_conta_payout", "appsell_dono_0065_lido_pela_0066")
    assert migration.down_revision == anterior.revision
    assert migration._READY_ATE_0065 == anterior._READY_ATE_0065
    assert migration._READY_ATE_0066.startswith(migration._READY_ATE_0065[:-2])
    assert migration.revision in migration._READY_ATE_0066
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY


def test_a_revisao_cabe_no_alembic_version(migration: ModuleType) -> None:
    assert len(migration.revision) <= 32


def test_toda_tabela_nova_tem_rls_forcada_e_nenhum_grant(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    for tabela in migration.TABELAS:
        nome = f"app_private.{tabela}"
        assert f"alter table {nome} enable row level security" in comandos
        assert f"alter table {nome} force row level security" in comandos
        assert f"revoke all on table {nome} from public,appsell_runtime" in comandos
        assert not any(c.startswith("grant") and nome in c for c in comandos)


def test_so_o_registro_e_o_descadastro_chegam_ao_runtime(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    concedidas = sorted(c for c in comandos if c.startswith("grant execute"))
    assert concedidas == [
        "grant execute on function app_private.record_email_consent"
        "(uuid,text,smallint,text,uuid) to appsell_runtime",
        "grant execute on function app_private.revoke_email_consent"
        "(uuid,text,uuid) to appsell_runtime",
        "grant execute on function app_private.unsubscribe_email(text) to appsell_runtime",
    ]


def test_o_transacional_e_a_lista_conhecida(migration: ModuleType) -> None:
    # Tipo novo da caixa de saída é opcional até alguém o pôr aqui. Se esta
    # lista mudar, a constraint de kind da email_outbox deve ter mudado junto.
    assert migration.TIPOS_TRANSACIONAIS == ("purchase_confirmed", "access_link_redeemed")


def test_o_claim_bloqueia_antes_de_escolher_candidatos(migration: ModuleType) -> None:
    trecho = migration.TRECHO_DO_CLAIM
    assert trecho.startswith(migration.ANCORA_DO_CLAIM.splitlines(keepends=True)[0])
    assert trecho.endswith(migration.ANCORA_DO_CLAIM.splitlines(keepends=True)[1])
    assert "not app_private.email_kind_is_transactional(queued.kind)" in trecho
    assert "status in ('pending','retry')" in trecho
    assert "queued.status='leased' and queued.lease_expires_at<=clock_timestamp()" in trecho
    # A âncora tem de existir, uma vez, no texto da função que a 0049 criou.
    caixa = (VERSOES / "0049_caixa_de_saida_de_email.py").read_text(encoding="utf-8")
    assert caixa.count(migration.ANCORA_DO_CLAIM) == 1


def test_a_descida_desfaz_o_claim_e_o_readiness(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    assert any(migration.TRECHO_DO_CLAIM in c and "claim_email_outbox" in c for c in comandos)
    for tabela in migration.TABELAS:
        assert f"drop table if exists app_private.{tabela}" in comandos


def test_todo_texto_de_consentimento_v1_existe_para_cada_finalidade(
    migration: ModuleType,
) -> None:
    finalidades = migration.FINALIDADES_DA_LOJA + migration.FINALIDADES_DA_PLATAFORMA
    assert set(migration.TEXTOS_V1) == set(finalidades)
    for corpo in migration.TEXTOS_V1.values():
        assert "descadastro" in corpo
        assert 20 <= len(corpo.encode()) <= 2000


def test_a_normalizacao_e_uma_so_e_nasce_antes_das_tabelas(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.upgrade)
    normaliza = next(
        i for i, c in enumerate(comandos) if "function app_private.normalize_email" in c
    )
    primeira_tabela = next(i for i, c in enumerate(comandos) if "create table" in c)
    assert normaliza < primeira_tabela
    # trim + lower só existe dentro de normalize_email.
    for comando in comandos:
        if "lower(btrim(" in comando:
            assert "function app_private.normalize_email" in comando


def test_a_supressao_guarda_o_sha_e_nao_o_endereco(migration: ModuleType) -> None:
    for sql in migration._CRIA_TABELAS:
        if "table app_private.email_suppression" in sql:
            assert "email_sha256" in sql
            assert "email_normalized" not in sql


def test_a_descida_derruba_tabelas_antes_das_funcoes(migration: ModuleType) -> None:
    comandos = _comandos_de(migration, migration.downgrade)
    ultima_tabela = max(i for i, c in enumerate(comandos) if c.startswith("drop table"))
    normaliza = next(i for i, c in enumerate(comandos) if "normalize_email" in c and "drop" in c)
    assert ultima_tabela < normaliza


def test_nao_toca_na_idempotencia_nem_no_caminho_da_compra(migration: ModuleType) -> None:
    # Pedido da Frente E (#357): a 0066 só reescreve o claim. A inserção de
    # purchase_confirmed (apply_checkout_fulfillment, 0049) e a dedupe_key
    # ficam como estão; o caminho ponta a ponta continua provado em
    # test_postgres_email_outbox.py, que roda contra o banco já na 0066.
    comandos = _comandos_de(migration, migration.upgrade)
    assert not any("apply_checkout_fulfillment" in c for c in comandos)
    assert not any("email_outbox_dedupe_key" in c for c in comandos)
    assert not any("alter table app_private.email_outbox" in c for c in comandos)
    assert "dedupe_key" not in migration.TRECHO_DO_CLAIM
    assert "insert" not in migration.TRECHO_DO_CLAIM
    reescritas = [c for c in comandos if "pg_get_functiondef" in c]
    assert sum(migration._CLAIM in c for c in reescritas) == 1
    assert len(reescritas) == 1 + len(migration._BASES_DO_READY)
