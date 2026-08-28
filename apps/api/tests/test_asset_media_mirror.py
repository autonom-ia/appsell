"""O catálogo do navegador e o do servidor precisam dizer o mesmo número.

Eles são duas tabelas em duas linguagens, e nada obrigava as duas a concordarem.
O limite do PDF chegou a existir em quatro lugares ao mesmo tempo; três já foram
transformados em derivação, e este teste cobre o quarto — o do navegador, que não
tem como derivar do Python.

Divergir aqui não dá erro: dá uma tela que aceita o arquivo e um servidor que
recusa depois do envio, ou o contrário.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from appsell_api.asset_media import SUPPORTED_MEDIA

_TS = Path(__file__).resolve().parents[3] / "lib" / "asset-media.ts"
_ENTRY = re.compile(r'extension:\s*"(?P<ext>[a-z0-9]+)",\s*maxBytes:\s*(?P<n>\d+)\s*\*\s*MIB')


def _browser_limits() -> dict[str, int]:
    source = _TS.read_text(encoding="utf-8")
    return {m["ext"]: int(m["n"]) * 1024 * 1024 for m in _ENTRY.finditer(source)}


def test_o_arquivo_do_navegador_foi_encontrado() -> None:
    # Sem isto, um caminho errado faria o teste passar sobre uma tabela vazia.
    assert _TS.is_file(), f"catálogo do navegador não encontrado em {_TS}"
    assert len(_browser_limits()) >= 9


def test_todo_formato_do_servidor_existe_no_navegador_com_o_mesmo_limite() -> None:
    navegador = _browser_limits()
    for media_type, spec in SUPPORTED_MEDIA.items():
        assert spec.extension in navegador, f"{media_type} existe no servidor e não no navegador"
        assert navegador[spec.extension] == spec.max_bytes, (
            f"{media_type}: navegador aceita {navegador[spec.extension]} bytes "
            f"e servidor aceita {spec.max_bytes}"
        )


def test_o_navegador_nao_oferece_formato_que_o_servidor_recusa() -> None:
    servidor = {spec.extension for spec in SUPPORTED_MEDIA.values()}
    for extensao in _browser_limits():
        assert extensao in servidor, f".{extensao} é oferecido na tela e o servidor não conhece"


def test_o_banco_concorda_com_o_catalogo_sobre_apresentar_e_baixar() -> None:
    """As duas regras que decidem o papel do material, espelhadas.

    `player_renders_media` e `asset_media_downloadable` derivam do catálogo por
    extensão, e o revisor apontou o risco: se o catálogo ganhar outro formato
    apresentável, ou outro que o aluno não baixe, o SQL continuaria respondendo
    pelo par antigo sem nada acusar.

    Este teste é a rede. Ele compara formato a formato contra
    `SUPPORTED_MEDIA`, que é a fonte de onde o navegador e a API já derivam.
    """
    import os

    import psycopg

    admin_url = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("isolated PostgreSQL environment is required")

    with psycopg.connect(admin_url) as connection:
        for media_type, spec in SUPPORTED_MEDIA.items():
            apresenta = connection.execute(
                "select app_private.player_renders_media(%s)", (media_type,)
            ).fetchone()
            baixa = connection.execute(
                "select app_private.asset_media_downloadable(%s)", (media_type,)
            ).fetchone()
            assert apresenta is not None and baixa is not None
            # Derivado de `support_material_only`, não do par PDF/MP4 escrito à
            # mão: repetir a lista faria o teste concordar com o SQL por
            # construção, justamente onde ele deveria discordar.
            assert apresenta[0] is not spec.support_material_only, (
                f"{media_type}: catálogo diz support_material_only="
                f"{spec.support_material_only} e o banco diz que o player "
                f"{'apresenta' if apresenta[0] else 'não apresenta'}"
            )
            assert baixa[0] is spec.downloadable, (
                f"{media_type}: catálogo diz downloadable={spec.downloadable} "
                f"e o banco diz {baixa[0]}"
            )
