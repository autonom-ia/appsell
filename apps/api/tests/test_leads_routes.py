"""Rotas da base de leads e do consentimento de e-mail (#619), sem banco.

O SQL de verdade está em `test_postgres_base_de_leads.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError

from appsell_api import email_consent_routes, leads_routes

LOJA = UUID("ee000000-0000-4000-8000-0000000007a1")
PESSOA = UUID("ee000000-0000-4000-8000-0000000007a2")
PERIODO = "from=2026-10-01&to=2026-10-31"


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
    body: bytes = b""


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
    corpo: bytes = b"",
) -> FastAPI:
    auth = _Auth(chamadas, resultado)

    async def autenticar(request: object, capacidade: str) -> tuple[_Auth, _Interno]:
        capacidades.append(capacidade)
        return auth, _Interno(_Envelope(_Identidade(PESSOA), uuid4(), LOJA), corpo)

    async def exigir_loja(internal: object, seller_id: UUID) -> None:
        assert seller_id == LOJA

    async def corpo_do_pedido(model: type[BaseModel], internal: _Interno) -> Any:
        try:
            return model.model_validate_json(internal.body)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail="invalid_request_body") from error

    for modulo in (leads_routes, email_consent_routes):
        monkeypatch.setattr(modulo, "_authenticate", autenticar)
        monkeypatch.setattr(modulo, "_require_seller", exigir_loja)
    monkeypatch.setattr(email_consent_routes, "_model_from_body", corpo_do_pedido)
    app = FastAPI()
    app.include_router(leads_routes.router)
    app.include_router(email_consent_routes.router)
    return app


async def _pedir(app: FastAPI, metodo: str, caminho: str) -> httpx.Response:
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://api.test") as cliente:
        return await cliente.request(metodo, caminho)


@pytest.mark.asyncio
async def test_leitura_usa_a_capacidade_de_leitura_e_o_produto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    capacidades: list[str] = []
    resultado = {"schema_version": 1, "rows": []}
    app = _app(monkeypatch, chamadas, resultado, capacidades)
    produto = uuid4()
    resposta = await _pedir(app, "GET", f"/v1/me/sellers/{LOJA}/leads?{PERIODO}&product={produto}")
    assert resposta.status_code == 200
    assert resposta.json() == resultado
    assert capacidades == ["growth.leads.read"]
    assert "app_private.read_store_leads" in chamadas[0]["sql"]
    assert chamadas[0]["actor"] == PESSOA and chamadas[0]["product"] == produto


@pytest.mark.asyncio
async def test_export_usa_a_capacidade_de_export(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []
    capacidades: list[str] = []
    app = _app(monkeypatch, chamadas, {"schema_version": 1, "rows": []}, capacidades)
    resposta = await _pedir(app, "GET", f"/v1/me/sellers/{LOJA}/leads/export?{PERIODO}")
    assert resposta.status_code == 200
    assert capacidades == ["growth.leads.export"]
    assert "app_private.export_store_leads" in chamadas[0]["sql"]
    assert chamadas[0]["request_id"] is not None


@pytest.mark.asyncio
async def test_periodo_ou_produto_invalido_nao_chega_ao_banco(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    app = _app(monkeypatch, chamadas, {}, [])
    for query in ("from=2026-10-31&to=2026-10-01", "from=x&to=y", f"{PERIODO}&product=nao-uuid"):
        resposta = await _pedir(app, "GET", f"/v1/me/sellers/{LOJA}/leads?{query}")
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
        ("XX000", 503, "leads_unavailable"),
    ],
)
async def test_erros_do_banco_viram_respostas_sem_detalhe(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str, status: int, detalhe: str
) -> None:
    erro = DBAPIError("select", {}, _ErroDoBanco(sqlstate))
    app = _app(monkeypatch, [], erro, [])
    resposta = await _pedir(app, "GET", f"/v1/me/sellers/{LOJA}/leads/export?{PERIODO}")
    assert resposta.status_code == status
    assert resposta.json() == {"detail": detalhe}


@pytest.mark.asyncio
async def test_consentimento_grava_store_news_v1_com_a_origem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    capacidades: list[str] = []
    app = _app(monkeypatch, chamadas, None, capacidades, b'{"source":"checkout"}')
    resposta = await _pedir(app, "POST", f"/v1/me/sellers/{LOJA}/email-consent")
    assert resposta.status_code == 204
    assert capacidades == ["notification.email.consent"]
    chamada = chamadas[0]
    assert "app_private.record_email_consent" in chamada["sql"]
    assert (chamada["purpose"], chamada["version"], chamada["source"]) == (
        "store_news",
        1,
        "checkout",
    )
    # O e-mail nunca vem do pedido: a função lê da identidade do contexto.
    assert "email" not in chamada


@pytest.mark.asyncio
async def test_consentimento_recusa_origem_ou_campo_desconhecido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for corpo in (b'{"source":"storefront_form"}', b'{"source":"account","email":"x@y.z"}', b"{}"):
        chamadas: list[dict[str, Any]] = []
        app = _app(monkeypatch, chamadas, None, [], corpo)
        resposta = await _pedir(app, "POST", f"/v1/me/sellers/{LOJA}/email-consent")
        assert resposta.status_code == 422, corpo
        assert chamadas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("sqlstate", "status"), [("28000", 403), ("XX000", 503)])
async def test_consentimento_erro_do_banco_sem_detalhe(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str, status: int
) -> None:
    erro = DBAPIError("select", {}, _ErroDoBanco(sqlstate))
    app = _app(monkeypatch, [], erro, [], b'{"source":"account"}')
    resposta = await _pedir(app, "POST", f"/v1/me/sellers/{LOJA}/email-consent")
    assert resposta.status_code == status


def test_o_texto_da_caixa_no_web_e_o_store_news_v1_do_banco() -> None:
    # O consentimento guarda o sha do texto v1 como prova: o que a pessoa vê
    # na caixa tem de ser exatamente ele.
    raiz = Path(__file__).resolve().parents[3]
    caminho = raiz / "apps/api/migrations/versions/0066_consentimento_de_email.py"
    spec = importlib_util.spec_from_file_location("appsell_consentimento_0066", caminho)
    assert spec is not None and spec.loader is not None
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    texto_do_web = (raiz / "lib/email-consent/texto.ts").read_text()
    assert f'"{modulo.TEXTOS_V1["store_news"]}"' in texto_do_web
    assert email_consent_routes.VERSAO_DO_TEXTO == 1
