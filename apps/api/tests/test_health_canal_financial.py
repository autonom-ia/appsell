"""O canal Financial no `startup` e no `ready` (#465).

Com loja vinculada e o canal sem exatamente uma identidade, produto novo fica
sem mapping e a compra dele vai para `dead_letter`. A decisão da Orquestração:

* `startup`, antes de a task ter ficado pronta: reprova, e a task nova nunca
  entra. A anterior segue servindo;
* `ready`, em regime: o status HTTP não muda (um 503 tiraria todas as tasks de
  rotação de uma vez), aparece `"financial_channel": false` e o log
  `appsell.financial_channel.unresolved` sai em `error` na transição e depois no
  máximo uma vez a cada 5 min;
* sem loja vinculada, a função do banco responde verdadeiro e nada reprova.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from appsell_api import main

UNRESOLVED = "appsell.financial_channel.unresolved"


def _sempre(valor: object):  # type: ignore[no-untyped-def]
    async def _responde(*_args: object, **_kwargs: object) -> object:
        return valor

    return _responde


@pytest.fixture
def estado_limpo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(main.app.state, "db_engine", None, raising=False)
    monkeypatch.setattr(
        main,
        "_dependencias_prontas",
        _sempre({"pronto": True, "database": True, "storage": True, "pdf_sandbox": True}),
    )
    for nome in ("ever_ready", "financial_channel_ok", "financial_channel_logged_at"):
        monkeypatch.setattr(main.app.state, nome, None, raising=False)
    yield


def _canal(monkeypatch: pytest.MonkeyPatch, valor: bool | None) -> None:
    monkeypatch.setattr(main, "financial_channel_ready", _sempre(valor))


async def _get(caminho: str) -> tuple[int, dict[str, object]]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(caminho)
    return response.status_code, response.json()


@pytest.mark.asyncio
async def test_o_startup_reprova_a_task_nova_com_o_canal_ambiguo(
    estado_limpo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pega a task nova promovida com o canal quebrado: todo produto novo iria
    # para dead_letter sem ninguém ver.
    _canal(monkeypatch, False)

    status_code, corpo = await _get("/health/startup")

    assert status_code == 503
    assert corpo == {"status": "starting", "ever_ready": False, "financial_channel": False}
    assert not main.app.state.ever_ready


@pytest.mark.asyncio
async def test_o_ready_em_regime_segue_200_e_loga_com_limite(
    estado_limpo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pega dois erros: 503 em regime (derrubaria a API inteira) e log a cada
    # sondagem (spam).
    _canal(monkeypatch, True)
    status_code, _ = await _get("/health/startup")
    assert status_code == 200

    relogio = [1000.0]
    monkeypatch.setattr(main, "_relogio", lambda: relogio[0])
    _canal(monkeypatch, False)
    respostas = []
    with capture_logs() as logs:
        for avanco in (0.0, 10.0, 10.0, 290.0, 10.0):
            relogio[0] += avanco
            respostas.append(await _get("/health/ready"))

    assert [codigo for codigo, _ in respostas] == [200] * 5
    assert all(corpo["financial_channel"] is False for _, corpo in respostas)
    erros = [log for log in logs if log["event"] == UNRESOLVED]
    # Transição em t=1000 e de novo só em t=1310 (≥ 300 s depois).
    assert len(erros) == 2
    assert all(log["log_level"] == "error" for log in erros)
    status_code, _ = await _get("/health/startup")
    assert status_code == 200, "a task que já serviu não sai pelo canal"


@pytest.mark.asyncio
async def test_o_canal_que_volta_e_que_cai_de_novo_loga_na_hora(
    estado_limpo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pega o limite de 5 min engolindo uma transição nova.
    main.app.state.ever_ready = True
    with capture_logs() as logs:
        for valor in (False, True, False):
            _canal(monkeypatch, valor)
            await _get("/health/ready")

    eventos = [log["event"] for log in logs if "financial_channel" in log["event"]]
    assert eventos == [UNRESOLVED, "appsell.financial_channel.resolved", UNRESOLVED]


@pytest.mark.asyncio
async def test_sem_loja_vinculada_nada_reprova(
    estado_limpo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sem loja vinculada a função do banco responde verdadeiro (dev, demo,
    # banco novo): o startup aprova e o ready não loga.
    _canal(monkeypatch, True)
    with capture_logs() as logs:
        startup_status, _ = await _get("/health/startup")
        ready_status, corpo = await _get("/health/ready")

    assert startup_status == 200
    assert ready_status == 200
    assert corpo["financial_channel"] is True
    assert not [log for log in logs if log["event"] == UNRESOLVED]


@pytest.mark.asyncio
async def test_sem_resposta_do_banco_o_canal_nao_decide(
    estado_limpo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Banco fora já reprova por `database`; o desconhecido não vira "canal
    # quebrado" nem reprova o startup de uma task que, fora isso, está pronta.
    _canal(monkeypatch, None)

    startup_status, _ = await _get("/health/startup")
    ready_status, corpo = await _get("/health/ready")

    assert startup_status == 200
    assert ready_status == 200
    assert "financial_channel" not in corpo
