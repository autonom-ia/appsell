"""O pré-voo de donos fora do banco: a URL e o erro de uso (#509).

A consulta rodando de verdade é provada em
`test_postgres_dono_da_conta_payout.py`.
"""

from __future__ import annotations

import pytest

from appsell_api import preflight_donos


def test_aceita_a_url_do_sqlalchemy_da_api() -> None:
    url = preflight_donos.url_do_psycopg("postgresql+asyncpg://u:p@db:5432/appsell")
    assert url == "postgresql://u:p@db:5432/appsell"


def test_sem_url_e_erro_de_uso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(preflight_donos.VARIAVEL, raising=False)
    with pytest.raises(SystemExit) as saida:
        preflight_donos.main()
    assert saida.value.code == 2


def test_so_le_o_catalogo() -> None:
    consulta = preflight_donos.FORA_DA_CONVENCAO.lower()
    for escrita in ("insert ", "update ", "delete ", "alter ", "create ", "drop "):
        assert escrita not in consulta
    assert "deptype = 'e'" in consulta
