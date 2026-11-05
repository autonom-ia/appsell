"""Worker de retenção da atribuição (#563), sem banco.

A limpeza de verdade, com duas réplicas, está em
`test_postgres_links_e_atribuicao.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from appsell_api import attribution_retention_worker as worker


class _Sessao:
    def __init__(self, respostas: list[object], chamadas: list[dict[str, Any]]) -> None:
        self._respostas = respostas
        self._chamadas = chamadas

    async def scalar(self, statement: object, params: dict[str, Any]) -> object:
        self._chamadas.append({"sql": str(statement), **params})
        resposta = self._respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield


def _fabrica(respostas: list[object], chamadas: list[dict[str, Any]]) -> Any:
    @asynccontextmanager
    async def fabrica() -> AsyncIterator[_Sessao]:
        yield _Sessao(respostas, chamadas)

    return fabrica


@pytest.mark.asyncio
async def test_roda_lotes_ate_sobrar_menos_que_um_lote() -> None:
    chamadas: list[dict[str, Any]] = []
    respostas: list[object] = [
        {"skipped": False, "without_sale": worker.LOTE, "expired": 0},
        {"skipped": False, "without_sale": 3, "expired": 1},
    ]
    resultado = await worker.limpar_uma_vez(_fabrica(respostas, chamadas))
    assert resultado == {"skipped": False, "without_sale": worker.LOTE + 3, "expired": 1}
    assert len(chamadas) == 2
    assert all("purge_checkout_attribution" in c["sql"] for c in chamadas)
    assert all(c["limite"] == worker.LOTE for c in chamadas)


@pytest.mark.asyncio
async def test_outra_replica_limpando_para_na_hora() -> None:
    chamadas: list[dict[str, Any]] = []
    respostas: list[object] = [{"skipped": True, "without_sale": 0, "expired": 0}]
    resultado = await worker.limpar_uma_vez(_fabrica(respostas, chamadas))
    assert resultado["skipped"] is True
    assert len(chamadas) == 1


@pytest.mark.asyncio
async def test_teto_de_lotes_por_rodada() -> None:
    cheio = {"skipped": False, "without_sale": worker.LOTE, "expired": worker.LOTE}
    respostas: list[object] = [dict(cheio) for _ in range(worker.MAXIMO_DE_LOTES_POR_RODADA + 5)]
    chamadas: list[dict[str, Any]] = []
    await worker.limpar_uma_vez(_fabrica(respostas, chamadas))
    assert len(chamadas) == worker.MAXIMO_DE_LOTES_POR_RODADA


@pytest.mark.asyncio
async def test_banco_fora_registra_e_o_laco_para_no_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Erro(Exception):
        sqlstate = "08006"

    respostas: list[object] = [DBAPIError("select", {}, _Erro())] + [
        {"skipped": True, "without_sale": 0, "expired": 0} for _ in range(1000)
    ]
    app = FastAPI()
    app.state.db_session_factory = _fabrica(respostas, [])
    monkeypatch.setattr(worker, "INTERVALO_SEGUNDOS", 0.01)
    stop = asyncio.Event()
    with capture_logs() as logs:
        tarefa = asyncio.create_task(worker.run_attribution_retention_loop(app, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(tarefa, timeout=1)
    eventos = [log["event"] for log in logs]
    assert "attribution_retention_retry" in eventos
    # Só contagens no log: nada de loja, compra ou campanha.
    for log in logs:
        assert set(log) <= {
            "event",
            "log_level",
            "error_type",
            "skipped",
            "without_sale",
            "expired",
        }


@pytest.mark.asyncio
async def test_sem_banco_configurado_nao_roda() -> None:
    app = FastAPI()
    await asyncio.wait_for(worker.run_attribution_retention_loop(app, asyncio.Event()), timeout=1)
