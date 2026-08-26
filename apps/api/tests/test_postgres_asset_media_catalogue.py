"""O catálogo de mídia é o mesmo nas três camadas.

Ele existe em três linguagens: TypeScript no navegador, Python na API e SQL no
banco. Nada obriga os três a concordarem, e discordar não dá erro — dá uma tela
que aceita o arquivo e um servidor que recusa depois do envio, ou o contrário.

Foi o que aconteceu em 26/08/2026: o teto do PDF subiu para 100 MiB no
navegador e na API, o deploy passou verde nas duas, e o banco continuou em
10 MiB. O upload morria com `errcode 22023`, que vira 422 na API e "não foi
possível preparar o arquivo" na tela — três traduções depois, sem nenhuma
mencionar tamanho.

`test_asset_media_mirror.py` já compara navegador × API. Este arquivo fecha o
triângulo, que é onde o defeito passou.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

from appsell_api.asset_media import SUPPORTED_MEDIA

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="isolated PostgreSQL environment is required")

_TS = Path(__file__).resolve().parents[3] / "lib" / "asset-media.ts"
_ENTRY = re.compile(r'extension:\s*"(?P<ext>[a-z0-9]+)",\s*maxBytes:\s*(?P<n>\d+)\s*\*\s*MIB')


def _database_catalogue() -> dict[str, int]:
    """A tabela de formatos como o banco a enxerga, por extensão."""
    assert ADMIN_URL
    catalogue: dict[str, int] = {}
    with psycopg.connect(ADMIN_URL) as connection:
        for media_type in SUPPORTED_MEDIA:
            rows = connection.execute(
                "select extension, max_bytes from app_private.asset_media_spec(%s)",
                (media_type,),
            ).fetchall()
            assert len(rows) == 1, f"{media_type}: o banco devolveu {len(rows)} linhas"
            catalogue[str(rows[0][0])] = int(rows[0][1])
    return catalogue


def _browser_catalogue() -> dict[str, int]:
    assert _TS.is_file(), f"catálogo do navegador não encontrado em {_TS}"
    return {m["ext"]: int(m["n"]) * 1024 * 1024 for m in _ENTRY.finditer(_TS.read_text("utf-8"))}


def test_o_banco_conhece_todo_formato_que_a_api_conhece() -> None:
    banco = _database_catalogue()
    for media_type, spec in SUPPORTED_MEDIA.items():
        assert spec.extension in banco, f"{media_type} existe na API e não no banco"
        assert banco[spec.extension] == spec.max_bytes, (
            f"{media_type}: banco aceita {banco[spec.extension]} bytes "
            f"e API aceita {spec.max_bytes}"
        )


def test_o_navegador_concorda_com_o_banco() -> None:
    banco = _database_catalogue()
    navegador = _browser_catalogue()
    assert navegador, "nenhum formato lido do catálogo do navegador"
    for extensao, limite in navegador.items():
        assert extensao in banco, f".{extensao} é oferecido na tela e o banco não conhece"
        assert banco[extensao] == limite, (
            f".{extensao}: navegador aceita {limite} bytes e banco aceita {banco[extensao]}"
        )


def test_o_contrato_aceita_o_teto_e_recusa_um_byte_alem() -> None:
    """A checagem de borda em cada formato, feita pelo próprio banco.

    Comparar as tabelas prova que os números batem; só exercitar
    `asset_media_contract_ok` prova que o número é o que de fato decide.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for media_type, spec in SUPPORTED_MEDIA.items():
            nome = f"material.{spec.extension}"
            no_teto = connection.execute(
                "select app_private.asset_media_contract_ok(%s,%s,%s)",
                (media_type, nome, spec.max_bytes),
            ).fetchone()
            alem = connection.execute(
                "select app_private.asset_media_contract_ok(%s,%s,%s)",
                (media_type, nome, spec.max_bytes + 1),
            ).fetchone()
            assert no_teto is not None and no_teto[0] is True, (
                f"{media_type}: o banco recusa o arquivo no próprio teto"
            )
            assert alem is not None and alem[0] is False, (
                f"{media_type}: o banco aceita um byte além do teto"
            )


def test_o_pdf_de_um_playbook_real_passa() -> None:
    """O caso concreto que quebrou: 83 páginas, 12.222.444 bytes.

    O nome tem espaço e parênteses de propósito — foi o arquivo real, e um nome
    assim chegou a ser suspeito antes de a medição apontar o limite.
    """
    assert ADMIN_URL
    nome = "Playbook_HUB2YOU_02_Gerador_Cotacoes_Comparativas_PROVA_v0.6 (1).pdf"
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select app_private.asset_media_contract_ok(%s,%s,%s)",
            ("application/pdf", nome, 12_222_444),
        ).fetchone()
    assert row is not None and row[0] is True


def test_o_intent_nao_repete_mais_o_catalogo() -> None:
    """A cópia inline da regra do PDF não pode voltar.

    Ela sobreviveu porque ninguém olhava para ela: a linha seguinte já chamava
    o catálogo, então a cópia parecia inofensiva.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select prosrc from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='app_private' and p.proname='create_asset_upload_intent'"
        ).fetchone()
    assert row is not None
    fonte = str(row[0])
    assert "asset_media_contract_ok" in fonte
    # Sem fronteira, `10485760` casaria dentro de `104857600` — que é o teto de
    # vídeo, legítimo. Essa confusão já inflou um diagnóstico nesta base.
    assert not re.search(r"(?<!\d)10485760(?!\d)", fonte), (
        "a regra do PDF voltou a ser transcrita dentro de create_asset_upload_intent"
    )
