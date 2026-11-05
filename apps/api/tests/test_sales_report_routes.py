"""Rotas do relatório de vendas (#562), sem banco: a sessão é falsa aqui.

O SQL de verdade está em `test_postgres_relatorio_de_vendas.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from appsell_api import sales_report_routes
from appsell_api.sales_report_routes import periodo_da_query, router

LOJA = UUID("ee000000-0000-4000-8000-000000000731")
PESSOA = UUID("ee000000-0000-4000-8000-000000000732")


class _ErroDoBanco(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@dataclass
class _Identidade:
    user_id: UUID


@dataclass
class _Envelope:
    identity: _Identidade
    request_id: UUID
    seller_organization_id: UUID


@dataclass
class _Interno:
    envelope: _Envelope


class _Sessao:
    def __init__(self, chamadas: list[dict[str, Any]], resultado: object) -> None:
        self._chamadas = chamadas
        self._resultado = resultado

    async def scalar(self, statement: object, params: dict[str, Any]) -> object:
        self._chamadas.append({"sql": str(statement), **params})
        if isinstance(self._resultado, Exception):
            raise self._resultado
        return self._resultado


class _Auth:
    def __init__(self, chamadas: list[dict[str, Any]], resultado: object) -> None:
        self._chamadas = chamadas
        self._resultado = resultado

    @asynccontextmanager
    async def domain_session(self, envelope: object) -> AsyncIterator[_Sessao]:
        yield _Sessao(self._chamadas, self._resultado)


def _app(
    monkeypatch: pytest.MonkeyPatch,
    chamadas: list[dict[str, Any]],
    resultado: object,
    capacidades: list[str],
) -> FastAPI:
    auth = _Auth(chamadas, resultado)

    async def autenticar(request: object, capacidade: str) -> tuple[_Auth, _Interno]:
        capacidades.append(capacidade)
        return auth, _Interno(_Envelope(_Identidade(PESSOA), uuid4(), LOJA))

    async def exigir_loja(internal: object, seller_id: UUID) -> None:
        assert seller_id == LOJA

    monkeypatch.setattr(sales_report_routes, "_authenticate", autenticar)
    monkeypatch.setattr(sales_report_routes, "_require_seller", exigir_loja)
    app = FastAPI()
    app.include_router(router)
    return app


async def _get(app: FastAPI, caminho: str) -> httpx.Response:
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://api.test") as cliente:
        return await cliente.get(caminho)


def test_periodo_da_query_aceita_so_datas_iso_e_ordem() -> None:
    assert periodo_da_query("2026-10-01", "2026-10-31") == (date(2026, 10, 1), date(2026, 10, 31))
    # 366 dias de calendário, contando as duas pontas, ainda passam.
    assert periodo_da_query("2025-10-01", "2026-10-01") == (date(2025, 10, 1), date(2026, 10, 1))
    for de, ate in (
        ("2026-10-31", "2026-10-01"),
        ("01/10/2026", "2026-10-31"),
        ("2026-10-01T00:00", "2026-10-31"),
        ("2025-01-01", "2026-10-31"),
        ("2025-10-01", "2026-10-02"),
        (None, "2026-10-31"),
    ):
        assert periodo_da_query(de, ate) is None


@pytest.mark.asyncio
async def test_relatorio_usa_a_capacidade_de_leitura(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []
    capacidades: list[str] = []
    resultado = {"schema_version": 1, "rows": []}
    app = _app(monkeypatch, chamadas, resultado, capacidades)
    resposta = await _get(app, f"/v1/me/sellers/{LOJA}/sales/report?from=2026-10-01&to=2026-10-31")
    assert resposta.status_code == 200
    assert resposta.json() == resultado
    assert capacidades == ["commerce.sales.report.read"]
    assert "app_private.read_sales_report" in chamadas[0]["sql"]
    assert chamadas[0]["actor"] == PESSOA and chamadas[0]["product"] is None


@pytest.mark.asyncio
async def test_export_usa_a_capacidade_de_export(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []
    capacidades: list[str] = []
    app = _app(monkeypatch, chamadas, {"schema_version": 1, "rows": []}, capacidades)
    resposta = await _get(app, f"/v1/me/sellers/{LOJA}/sales/export?from=2026-10-01&to=2026-10-31")
    assert resposta.status_code == 200
    assert capacidades == ["commerce.sales.report.export"]
    assert "app_private.export_sales_rows" in chamadas[0]["sql"]
    assert chamadas[0]["request_id"] is not None


@pytest.mark.asyncio
async def test_periodo_ou_produto_invalido_nao_chega_ao_banco(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    app = _app(monkeypatch, chamadas, {}, [])
    base = f"/v1/me/sellers/{LOJA}/sales/report"
    for query in (
        "?from=2026-10-31&to=2026-10-01",
        "?from=2026-10-01",
        "?from=x&to=y",
        "?from=2026-10-01&to=2026-10-31&product=nao-uuid",
    ):
        resposta = await _get(app, base + query)
        assert resposta.status_code == 422, query
    assert chamadas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sqlstate", "status", "detalhe"),
    [
        ("42501", 403, "forbidden"),
        ("22023", 422, "invalid_period"),
        ("P4291", 429, "export_rate_limited"),
        ("P4131", 413, "export_too_large"),
        ("XX000", 503, "sales_report_unavailable"),
    ],
)
async def test_erros_do_banco_viram_respostas_sem_detalhe(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str, status: int, detalhe: str
) -> None:
    erro = DBAPIError("select", {}, _ErroDoBanco(sqlstate))
    app = _app(monkeypatch, [], erro, [])
    resposta = await _get(app, f"/v1/me/sellers/{LOJA}/sales/export?from=2026-10-01&to=2026-10-31")
    assert resposta.status_code == status
    assert resposta.json() == {"detail": detalhe}
