"""A 0082, lida como texto.

O SQL rodando de verdade é provado em `test_postgres_private_assets.py`
(`test_envio_pronto_nao_ocupa_a_vaga_de_envio_aberto`).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import pytest
from cadeia_de_migrations import anterior_na_cadeia, versoes

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


def _carregar(padrao: str, nome: str) -> ModuleType:
    caminho = next(VERSOES.glob(padrao))
    spec = importlib_util.spec_from_file_location(nome, caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration() -> ModuleType:
    return _carregar("*_pronto_sai_dos_abertos.py", "appsell_pronto_0082")


def _comandos(migration: ModuleType, direcao: Callable[[], None]) -> list[str]:
    executados: list[str] = []
    original = migration.op.execute
    migration.op.execute = executados.append
    try:
        direcao()
    finally:
        migration.op.execute = original
    return executados


def _corpo_vivo() -> str:
    """O corpo da intenção como o banco tem hoje: o da 0013 com o bloco da 0047.

    A 0035, a 0039 e a 0046 também reescrevem esta função, mas em linhas que
    não tocam os três limites de envio aberto.
    """
    fonte = next(VERSOES.glob("0013_*.py")).read_text()
    inicio = fonte.index("create or replace function app_private.create_asset_upload_intent(")
    fim = fonte.index("create or replace function app_private.confirm_asset_upload(")
    corpo = fonte[inicio:fim]
    cota = _carregar("0047_*.py", "appsell_cota_0047_para_0082")
    assert corpo.count(cota._ANCORA_EM_VOO) == 1
    return corpo.replace(cota._ANCORA_EM_VOO, cota._COM_BYTES_EM_VOO)


def test_esta_na_cadeia_e_cabe_no_alembic_version(migration: ModuleType) -> None:
    assert migration.revision in [caminho.stem for caminho in versoes()]
    assert anterior_na_cadeia(migration.revision) == migration.down_revision
    assert migration.down_revision == "0081_posicao_livre_ao_criar"
    assert len(migration.revision) <= 32


def test_o_readiness_aprende_a_versao(migration: ModuleType) -> None:
    anterior = _carregar("0081_*.py", "appsell_posicao_0081_para_0082")
    assert migration.READY_ANTES == anterior._READY_ATE_0081
    assert migration.READY_DEPOIS == migration.READY_ANTES[:-2] + f",'{migration.revision}'))"
    assert migration._READY_ATE_0082 == migration.READY_DEPOIS
    assert migration._BASES_DO_READY == anterior._BASES_DO_READY


def test_cada_ancora_existe_uma_vez_no_corpo_vivo(migration: ModuleType) -> None:
    # Pega: uma âncora que não bate com o corpo da 0013 + 0047, ou que casa com
    # mais de um dos três subselects (eles são quase iguais). Nos dois casos a
    # subida falharia em produção com "nao continha as ancoras exatamente uma vez".
    corpo = _corpo_vivo()
    assert len(migration.TROCAS) == 3
    for ancora, _filtro in migration.TROCAS:
        assert corpo.count(ancora) == 1
    ancoras = [ancora for ancora, _filtro in migration.TROCAS]
    assert len(set(ancoras)) == 3


def test_os_tres_limites_passam_a_ignorar_o_pronto(migration: ModuleType) -> None:
    corpo = _corpo_vivo()
    novo = corpo
    for ancora, filtro in migration.TROCAS:
        novo = novo.replace(ancora, filtro)
    assert novo.count("and upload.status<>'ready'") == 3
    assert novo.count(migration.MARCADOR) == 3
    for ancora, filtro in migration.TROCAS:
        assert novo.count(ancora) == 0
        assert novo.count(filtro) == 1
        assert migration.MARCADOR in filtro
    # Os limites continuam os mesmos: só muda o que conta como aberto.
    assert ")>=100 then" in migration.FILTRO_VENDEDOR
    assert ")>=10 then" in migration.FILTRO_PESSOA
    assert "app_private.seller_open_upload_quota_bytes()" in migration.FILTRO_BYTES
    # A descida devolve o corpo exatamente como estava.
    volta = novo
    for ancora, filtro in migration.TROCAS:
        volta = volta.replace(filtro, ancora)
    assert volta == corpo


def test_subida_e_descida_sao_idempotentes_pelo_marcador(migration: ModuleType) -> None:
    # O mesmo SQL pode rodar como hotfix antes do release: a subida pula se o
    # marcador já está, e a descida pula se ele não está.
    assert "> 0 then\n    return;" in migration.SOBE
    assert migration.MARCADOR in migration.SOBE
    assert migration.INTENCAO in migration.SOBE
    assert "= 0 then\n    return;" in migration.DESCE
    assert migration.MARCADOR in migration.DESCE
    for sql in (migration.SOBE, migration.DESCE):
        assert "__" not in sql


def test_nao_muda_dono_grant_nem_derruba_funcao(migration: ModuleType) -> None:
    subida = _comandos(migration, migration.upgrade)
    descida = _comandos(migration, migration.downgrade)
    assert subida[0] == descida[0] == "set local lock_timeout = '5s'"
    assert subida[1] == migration.SOBE
    assert descida[-1] == migration.DESCE
    for direcao in (subida, descida):
        assert not any("owner to" in comando for comando in direcao)
        assert not any(comando.lstrip().startswith("drop ") for comando in direcao)
        assert not any("grant " in comando or "revoke " in comando for comando in direcao)
