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

from appsell_api.asset_media import SUPPORTED_MEDIA

_TS = Path(__file__).resolve().parents[3] / "lib" / "asset-media.ts"
_ENTRY = re.compile(
    r'extension:\s*"(?P<ext>[a-z0-9]+)",\s*maxBytes:\s*(?P<n>\d+)\s*\*\s*MIB'
)


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
        assert spec.extension in navegador, (
            f"{media_type} existe no servidor e não no navegador"
        )
        assert navegador[spec.extension] == spec.max_bytes, (
            f"{media_type}: navegador aceita {navegador[spec.extension]} bytes "
            f"e servidor aceita {spec.max_bytes}"
        )


def test_o_navegador_nao_oferece_formato_que_o_servidor_recusa() -> None:
    servidor = {spec.extension for spec in SUPPORTED_MEDIA.values()}
    for extensao in _browser_limits():
        assert extensao in servidor, (
            f".{extensao} é oferecido na tela e o servidor não conhece"
        )
