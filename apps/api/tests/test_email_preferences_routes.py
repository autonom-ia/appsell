"""A rota de descadastro sem login (#561), sem banco: a função SQL é falsa aqui.

O SQL de verdade está em `test_postgres_consentimento_de_email.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import DBAPIError

from appsell_api.email_preferences_routes import router

TOKEN = "t" * 43


class _Auth:
    def require_service_credential(self, request: Request) -> None:
        if request.headers.get("x-appsell-service-credential") != "ok":
            raise HTTPException(status_code=401, detail="unauthorized")


class _Sessao:
    def __init__(self, chamadas: list[dict[str, Any]], resultado: object) -> None:
        self._chamadas = chamadas
        self._resultado = resultado

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield

    async def scalar(self, statement: object, params: dict[str, Any]) -> object:
        self._chamadas.append({"sql": str(statement), **params})
        if isinstance(self._resultado, Exception):
            raise self._resultado
        return self._resultado


def _app(chamadas: list[dict[str, Any]], resultado: object = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = _Auth()

    @asynccontextmanager
    async def fabrica() -> AsyncIterator[_Sessao]:
        yield _Sessao(chamadas, resultado)

    app.state.db_session_factory = fabrica
    return app


async def _post(app: FastAPI, corpo: bytes, credencial: str = "ok") -> httpx.Response:
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://api.test") as cliente:
        return await cliente.post(
            "/v1/public/email/unsubscribe",
            content=corpo,
            headers={
                "x-appsell-service-credential": credencial,
                "content-type": "application/json",
            },
        )


@pytest.mark.asyncio
async def test_exige_a_credencial_de_servico() -> None:
    chamadas: list[dict[str, Any]] = []
    resposta = await _post(_app(chamadas), b'{"token":"' + TOKEN.encode() + b'"}', "errada")
    assert resposta.status_code == 401
    assert chamadas == []


@pytest.mark.asyncio
async def test_descadastra_pelo_token() -> None:
    chamadas: list[dict[str, Any]] = []
    resposta = await _post(_app(chamadas), b'{"token":"' + TOKEN.encode() + b'"}')
    assert resposta.status_code == 204
    assert len(chamadas) == 1
    assert "app_private.unsubscribe_email" in chamadas[0]["sql"]
    assert chamadas[0]["token"] == TOKEN


@pytest.mark.asyncio
async def test_token_desconhecido_tem_a_mesma_resposta() -> None:
    chamadas: list[dict[str, Any]] = []
    resposta = await _post(_app(chamadas, resultado=False), b'{"token":"' + TOKEN.encode() + b'"}')
    assert resposta.status_code == 204
    assert resposta.content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corpo",
    [
        b"",
        b"nao e json",
        b"[]",
        b'{"token":1}',
        b'{"token":"curto"}',
        b'{"token":"' + b"x" * 129 + b'"}',
        b'{"token":"' + TOKEN.encode() + b'","extra":1}',
    ],
)
async def test_corpo_fora_do_formato_nao_chega_ao_banco_e_responde_igual(corpo: bytes) -> None:
    chamadas: list[dict[str, Any]] = []
    resposta = await _post(_app(chamadas), corpo)
    assert resposta.status_code == 204
    assert chamadas == []


@pytest.mark.asyncio
async def test_corpo_grande_e_recusado() -> None:
    chamadas: list[dict[str, Any]] = []
    resposta = await _post(_app(chamadas), b'{"token":"' + b"x" * 2048 + b'"}')
    assert resposta.status_code == 413
    assert chamadas == []


@pytest.mark.asyncio
async def test_banco_fora_responde_503_sem_ecoar_o_token() -> None:
    chamadas: list[dict[str, Any]] = []
    erro = DBAPIError("select", {}, Exception("falhou"))
    resposta = await _post(_app(chamadas, resultado=erro), b'{"token":"' + TOKEN.encode() + b'"}')
    assert resposta.status_code == 503
    assert TOKEN not in resposta.text
