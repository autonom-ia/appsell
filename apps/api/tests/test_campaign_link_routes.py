"""Rotas dos links UTM e da atribuição (#563), sem banco: a sessão é falsa aqui.

O SQL de verdade está em `test_postgres_links_e_atribuicao.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from appsell_api import campaign_link_routes
from appsell_api.campaign_link_routes import periodo_da_query, public_router, router

LOJA = UUID("ee000000-0000-4000-8000-000000000761")
PESSOA = UUID("ee000000-0000-4000-8000-000000000762")
LINK = UUID("ee000000-0000-4000-8000-000000000763")


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

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield


class _Auth:
    def __init__(self, chamadas: list[dict[str, Any]], resultado: object) -> None:
        self._chamadas = chamadas
        self._resultado = resultado
        self.credencial_conferida = 0

    @asynccontextmanager
    async def domain_session(self, envelope: object) -> AsyncIterator[_Sessao]:
        yield _Sessao(self._chamadas, self._resultado)

    def require_service_credential(self, request: Request) -> None:
        self.credencial_conferida += 1
        if request.headers.get("x-test-service") != "ok":
            raise HTTPException(status_code=401)


def _app(
    monkeypatch: pytest.MonkeyPatch,
    chamadas: list[dict[str, Any]],
    resultado: object,
    capacidades: list[str] | None = None,
    corpo: bytes = b"",
) -> tuple[FastAPI, _Auth]:
    auth = _Auth(chamadas, resultado)
    vistas = capacidades if capacidades is not None else []

    async def autenticar(request: object, capacidade: str) -> tuple[_Auth, _Interno]:
        vistas.append(capacidade)
        return auth, _Interno(_Envelope(_Identidade(PESSOA), uuid4(), LOJA), corpo)

    async def exigir_loja(internal: object, seller_id: UUID) -> None:
        assert seller_id == LOJA

    async def corpo_do_modelo(model: type[BaseModel], internal: _Interno) -> Any:
        try:
            return model.model_validate(json.loads(internal.body))
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid_body") from error

    monkeypatch.setattr(campaign_link_routes, "_authenticate", autenticar)
    monkeypatch.setattr(campaign_link_routes, "_require_seller", exigir_loja)
    monkeypatch.setattr(campaign_link_routes, "_model_from_body", corpo_do_modelo)
    app = FastAPI()
    app.include_router(router)
    app.include_router(public_router)
    app.state.internal_auth = auth

    @asynccontextmanager
    async def fabrica() -> AsyncIterator[_Sessao]:
        yield _Sessao(chamadas, resultado)

    app.state.db_session_factory = fabrica
    return app, auth


async def _pedir(
    app: FastAPI, metodo: str, caminho: str, corpo: object = None, servico: bool = True
) -> httpx.Response:
    transporte = httpx.ASGITransport(app=app)
    cabecalhos = {"x-test-service": "ok"} if servico else {}
    async with httpx.AsyncClient(transport=transporte, base_url="http://api.test") as cliente:
        conteudo = (
            None
            if corpo is None
            else (corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode())
        )
        return await cliente.request(metodo, caminho, content=conteudo, headers=cabecalhos)


def test_periodo_ate_366_dias_de_calendario() -> None:
    assert periodo_da_query("2025-10-01", "2026-10-01") is not None
    for de, ate in (("2025-10-01", "2026-10-02"), ("2026-10-02", "2026-10-01"), ("x", "y")):
        assert periodo_da_query(de, ate) is None


@pytest.mark.asyncio
async def test_lista_e_relatorio_usam_a_capacidade_de_leitura(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    capacidades: list[str] = []
    app, _ = _app(monkeypatch, chamadas, {"schema_version": 1, "links": []}, capacidades)
    assert (await _pedir(app, "GET", f"/v1/me/sellers/{LOJA}/campaign-links")).status_code == 200
    relatorio = await _pedir(
        app, "GET", f"/v1/me/sellers/{LOJA}/campaign-links/report?from=2026-10-01&to=2026-10-31"
    )
    assert relatorio.status_code == 200
    assert capacidades == ["growth.campaign_link.read", "growth.campaign_link.read"]
    assert "list_campaign_links" in chamadas[0]["sql"]
    assert "read_campaign_sales" in chamadas[1]["sql"]


@pytest.mark.asyncio
async def test_criar_valida_o_corpo_e_usa_a_capacidade_de_escrita(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    capacidades: list[str] = []
    corpo = json.dumps({"name": " Insta ", "utm_source": "instagram"}).encode()
    app, _ = _app(monkeypatch, chamadas, {"id": str(LINK)}, capacidades, corpo)
    resposta = await _pedir(app, "POST", f"/v1/me/sellers/{LOJA}/campaign-links")
    assert resposta.status_code == 201
    assert capacidades == ["growth.campaign_link.write"]
    assert chamadas[0]["name"] == "Insta" and chamadas[0]["source"] == "instagram"
    assert chamadas[0]["destination"] is None and chamadas[0]["request_id"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corpo",
    [
        {"name": "x"},
        {"name": "x", "utm_source": "a" * 101},
        {"name": "x", "utm_source": "a", "extra": 1},
        {"name": "", "utm_source": "a"},
    ],
)
async def test_corpo_invalido_nao_chega_ao_banco(
    monkeypatch: pytest.MonkeyPatch, corpo: dict[str, Any]
) -> None:
    chamadas: list[dict[str, Any]] = []
    app, _ = _app(monkeypatch, chamadas, {}, None, json.dumps(corpo).encode())
    resposta = await _pedir(app, "POST", f"/v1/me/sellers/{LOJA}/campaign-links")
    assert resposta.status_code == 422
    assert chamadas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sqlstate", "status", "detalhe"),
    [
        ("42501", 403, "forbidden"),
        ("22023", 422, "invalid_campaign_link"),
        ("P4130", 409, "too_many_campaign_links"),
        ("XX000", 503, "campaign_links_unavailable"),
    ],
)
async def test_erros_do_banco_viram_respostas_sem_detalhe(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str, status: int, detalhe: str
) -> None:
    erro = DBAPIError("select", {}, _ErroDoBanco(sqlstate))
    corpo = json.dumps({"name": "x", "utm_source": "a"}).encode()
    app, _ = _app(monkeypatch, [], erro, None, corpo)
    resposta = await _pedir(app, "POST", f"/v1/me/sellers/{LOJA}/campaign-links")
    assert resposta.status_code == status
    assert resposta.json() == {"detail": detalhe}


@pytest.mark.asyncio
async def test_arquivar_link_inexistente_responde_404(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _app(monkeypatch, [], False)
    caminho = f"/v1/me/sellers/{LOJA}/campaign-links/{LINK}/archive"
    assert (await _pedir(app, "POST", caminho)).status_code == 404
    app, _ = _app(monkeypatch, [], True)
    assert (await _pedir(app, "POST", caminho)).status_code == 204


@pytest.mark.asyncio
async def test_atribuicao_exige_credencial_de_servico(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []
    app, _ = _app(monkeypatch, chamadas, True)
    resposta = await _pedir(
        app, "POST", "/v1/public/tracking/checkout-attribution", {"x": 1}, servico=False
    )
    assert resposta.status_code == 401
    assert chamadas == []


@pytest.mark.asyncio
async def test_atribuicao_grava_o_sha_da_compra_e_nunca_loga_a_referencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    app, _ = _app(monkeypatch, chamadas, True)
    visita = int(datetime(2026, 11, 5, 12, tzinfo=UTC).timestamp())
    corpo = {
        "seller_organization_id": str(LOJA),
        "purchase_reference": "intent-segredo-123",
        "link_id": str(LINK),
        "utm_source": "instagram",
        "visited_at": visita,
    }
    with capture_logs() as logs:
        resposta = await _pedir(app, "POST", "/v1/public/tracking/checkout-attribution", corpo)
    assert resposta.status_code == 204
    assert chamadas[0]["sha"] == hashlib.sha256(b"intent-segredo-123").hexdigest()
    assert chamadas[0]["link"] == LINK and chamadas[0]["source"] == "instagram"
    assert chamadas[0]["visited_at"] == datetime(2026, 11, 5, 12, tzinfo=UTC)
    assert "intent-segredo-123" not in str(logs)
    assert logs[-1]["outcome"] == "recorded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corpo",
    [
        b"nao-json",
        {"seller_organization_id": "x", "purchase_reference": "a", "visited_at": 1},
        {"seller_organization_id": str(LOJA), "purchase_reference": "", "visited_at": 1},
        {"seller_organization_id": str(LOJA), "purchase_reference": "a", "visited_at": -1},
        {"seller_organization_id": str(LOJA), "purchase_reference": "a", "visited_at": 1, "x": 1},
    ],
)
async def test_atribuicao_malformada_responde_204_sem_banco(
    monkeypatch: pytest.MonkeyPatch, corpo: object
) -> None:
    chamadas: list[dict[str, Any]] = []
    app, _ = _app(monkeypatch, chamadas, True)
    resposta = await _pedir(app, "POST", "/v1/public/tracking/checkout-attribution", corpo)
    assert resposta.status_code == 204
    assert chamadas == []


@pytest.mark.asyncio
async def test_corpo_publico_grande_demais_responde_413(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _app(monkeypatch, [], True)
    resposta = await _pedir(app, "POST", "/v1/public/tracking/cookie-consent", b"x" * 3000)
    assert resposta.status_code == 413


@pytest.mark.asyncio
async def test_contagem_de_consentimento(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []
    app, _ = _app(monkeypatch, chamadas, True)
    corpo = {"seller_organization_id": str(LOJA), "text_version": "v1", "accepted": False}
    resposta = await _pedir(app, "POST", "/v1/public/tracking/cookie-consent", corpo)
    assert resposta.status_code == 204
    assert chamadas[0]["version"] == "v1" and chamadas[0]["accepted"] is False


@pytest.mark.asyncio
async def test_banco_fora_do_ar_responde_503(monkeypatch: pytest.MonkeyPatch) -> None:
    erro = DBAPIError("select", {}, _ErroDoBanco("08006"))
    app, _ = _app(monkeypatch, [], erro)
    corpo = {"seller_organization_id": str(LOJA), "text_version": "v1", "accepted": True}
    resposta = await _pedir(app, "POST", "/v1/public/tracking/cookie-consent", corpo)
    assert resposta.status_code == 503
