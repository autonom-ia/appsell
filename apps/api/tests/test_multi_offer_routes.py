"""Rotas da segunda oferta pela api, sem banco (#553, fase 2).

A lista de lojas é decidida aqui. Loja fora da lista recebe 403 antes de
qualquer sessão de domínio; env malformada recusa com 503; as recusas do banco
viram códigos estáveis (P4093, P4094) sem vazar texto do driver.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_api_catalog import SELLER_ID, FakeAuth, FakeResult, client_for

from appsell_api.catalog_routes import _domain_error
from appsell_api.multi_offer import MULTI_OFFER_SELLERS_ENV

PRODUCT_ID = uuid4()
OFFER_ID = uuid4()
CREATE_PATH = f"/v1/me/sellers/{SELLER_ID}/products/{PRODUCT_ID}/offers"
DEFAULT_PATH = f"{CREATE_PATH}/{OFFER_ID}/default"


def _create_body() -> bytes:
    return json.dumps(
        {
            "idempotency_key": str(uuid4()),
            "offer_id": str(uuid4()),
            "title": "Plano anual",
            "summary": "",
            "unit_amount_minor": 19700,
            "currency": "BRL",
            "billing_period": "yearly",
        }
    ).encode()


def _default_body() -> bytes:
    return json.dumps({"idempotency_key": str(uuid4()), "expected_offer_version": 2}).encode()


ROTAS = ((CREATE_PATH, _create_body), (DEFAULT_PATH, _default_body))


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("texto interno do driver")
        self.sqlstate = sqlstate
        self.constraint_name = None
        self.diag = SimpleNamespace(message_primary="texto interno do driver")


class _RecusaDoBanco(FakeAuth):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(seller_id=SELLER_ID, result=FakeResult())
        self.sqlstate = sqlstate

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[Any]:
        self.domain_sessions += 1

        async def recusa(*_args: Any) -> Any:
            raise DBAPIError("select", {}, _DriverError(self.sqlstate))

        yield SimpleNamespace(execute=recusa)


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), ROTAS)
async def test_loja_fora_da_lista_recebe_403_sem_sessao_de_dominio(
    monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    monkeypatch.setenv(MULTI_OFFER_SELLERS_ENV, str(uuid4()))
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.post(path, content=body())
    assert (response.status_code, response.json()) == (403, {"detail": "multi_offer_disabled"})
    assert auth.domain_sessions == 0
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), ROTAS)
async def test_lista_vazia_desliga_para_todas_as_lojas(
    monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    monkeypatch.delenv(MULTI_OFFER_SELLERS_ENV, raising=False)
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.post(path, content=body())
    assert response.status_code == 403
    assert auth.domain_sessions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), ROTAS)
async def test_env_malformada_recusa_com_503_em_vez_de_adivinhar(
    monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    monkeypatch.setenv(MULTI_OFFER_SELLERS_ENV, f"{SELLER_ID},hub2you")
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.post(path, content=body())
    assert (response.status_code, response.json()) == (
        503,
        {"detail": "multi_offer_misconfigured"},
    )
    assert auth.domain_sessions == 0


@pytest.mark.asyncio
async def test_loja_na_lista_cria_pela_funcao_com_o_corpo_assinado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MULTI_OFFER_SELLERS_ENV, str(SELLER_ID))
    offer_id = uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(one={"offer_id": offer_id, "offer_version": 1, "created": True}),
    )
    body = _create_body()
    async with client_for(auth) as client:
        response = await client.post(CREATE_PATH, content=body)
    assert response.status_code == 200
    assert response.json() == {"offer_id": str(offer_id), "offer_version": 1, "created": True}
    assert auth.capabilities == ["catalog.offer.create"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.create_product_offer(" in statement
    assert parameters["seller_id"] == SELLER_ID
    assert parameters["unit_amount_minor"] == 19700
    assert parameters["currency"] == "BRL"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "sqlstate", "detail"),
    [
        (CREATE_PATH, _create_body, "P4094", "offer_limit_reached"),
        (DEFAULT_PATH, _default_body, "P4093", "default_offer_locked"),
        (DEFAULT_PATH, _default_body, "28000", "forbidden"),
    ],
)
async def test_recusa_do_banco_vira_codigo_estavel_sem_texto_do_driver(
    monkeypatch: pytest.MonkeyPatch, path: str, body: Any, sqlstate: str, detail: str
) -> None:
    monkeypatch.setenv(MULTI_OFFER_SELLERS_ENV, str(SELLER_ID))
    auth = _RecusaDoBanco(sqlstate)
    async with client_for(auth) as client:
        response = await client.post(path, content=body())
    assert response.json() == {"detail": detail}
    assert response.status_code == (403 if sqlstate == "28000" else 409)
    assert "driver" not in response.text


def test_mapeamento_dos_codigos_novos() -> None:
    for sqlstate, detail in (("P4093", "default_offer_locked"), ("P4094", "offer_limit_reached")):
        mapped = _domain_error(SimpleNamespace(orig=_DriverError(sqlstate)))  # type: ignore[arg-type]
        assert (mapped.status_code, mapped.detail) == (409, detail)


@pytest.mark.asyncio
@pytest.mark.parametrize(("campo", "valor"), [("title", "Plano\x01"), ("summary", "a\nb")])
async def test_caractere_de_controle_recusado_antes_do_banco(
    monkeypatch: pytest.MonkeyPatch, campo: str, valor: str
) -> None:
    monkeypatch.setenv(MULTI_OFFER_SELLERS_ENV, str(SELLER_ID))
    corpo = json.loads(_create_body())
    corpo[campo] = valor
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.post(CREATE_PATH, content=json.dumps(corpo).encode())
    assert response.status_code == 422
    assert auth.session.calls == []
