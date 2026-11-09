"""Contrato da rota interna do pedido do checkout (#154, fatia 1).

Sessão roteirizada: a rota tem de exigir o worker do checkout (sujeito fixo,
sem loja no envelope) antes de qualquer SQL, mandar o comprador e o intent só
em hash ao banco e traduzir as recusas do banco em respostas estáveis.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from appsell_api.checkout_order_routes import CAPACIDADE, SUJEITO, router

SELLER_ID = UUID("77000000-0000-4000-8000-000000000001")
PRODUCT_ID = UUID("77000000-0000-4000-8000-000000000002")
PUBLICATION_ID = UUID("77000000-0000-4000-8000-000000000003")
OFFER_ID = UUID("77000000-0000-4000-8000-000000000004")
ORDER_ID = UUID("77000000-0000-4000-8000-000000000005")
WORKER_ID = UUID("77000000-0000-4000-8000-000000000006")
EXPIRA = datetime(2026, 11, 9, 12, 31, tzinfo=UTC)
CRIAR = f"/v1/internal/sellers/{SELLER_ID}/checkout-orders"
LIGAR = f"/v1/internal/sellers/{SELLER_ID}/checkout-orders/{ORDER_ID}/intent"


class FakeDbError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("select", {}, FakeDbError(sqlstate))


class ScriptedSession:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _next(self, statement: Any, parameters: dict[str, Any] | None) -> Any:
        self.calls.append((str(statement), parameters or {}))
        assert self.script, "a rota fez mais SQL do que o teste roteirizou"
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        row = self._next(statement, parameters)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row))

    async def scalar(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return self._next(statement, parameters)


class FakeAuth:
    def __init__(
        self, *, script: list[Any], seller_id: UUID | None = None, subject: str = SUJEITO
    ) -> None:
        self.session = ScriptedSession(script)
        self.seller_id = seller_id
        self.subject = subject
        self.capabilities: list[str] = []
        self.rejected_requests = 0

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=WORKER_ID, sub=self.subject),
                seller_organization_id=self.seller_id,
                request_id=uuid4(),
            ),
            body=body,
            consume_rejected_request=self.consume_rejected_request,
        )

    async def consume_rejected_request(self) -> None:
        self.rejected_requests += 1

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[ScriptedSession]:
        yield self.session


@asynccontextmanager
async def client_for(auth: FakeAuth) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        yield client


def corpo(**mudancas: Any) -> bytes:
    valor = {
        "product_id": str(PRODUCT_ID),
        "publication_id": str(PUBLICATION_ID),
        "unit_amount_minor": 9700,
        "currency": "BRL",
        "billing_period": "one_time",
        "platform_fee_minor": 582,
        "seller_net_minor": 9118,
        "plan_key": "launch",
        "buyer_identity_sub": "cognito-sub-1",
        **mudancas,
    }
    return json.dumps(valor, separators=(",", ":")).encode()


def linha_gravada() -> dict[str, Any]:
    return {
        "order_id": ORDER_ID,
        "offer_id": OFFER_ID,
        "offer_version": 3,
        "charged_minor": 9700,
        "expires_at": EXPIRA,
    }


@pytest.mark.asyncio
async def test_grava_o_pedido_com_o_comprador_so_em_hash() -> None:
    auth = FakeAuth(script=[linha_gravada()])
    async with client_for(auth) as client:
        resposta = await client.post(CRIAR, content=corpo())
    assert resposta.status_code == 201
    assert resposta.json() == {
        "order_id": str(ORDER_ID),
        "offer_id": str(OFFER_ID),
        "offer_version": 3,
        "charged_minor": 9700,
        "expires_at": EXPIRA.isoformat(),
    }
    assert auth.capabilities == [CAPACIDADE]
    sql, params = auth.session.calls[0]
    assert "app_private.create_checkout_order(" in sql
    assert params["seller"] == SELLER_ID
    assert params["product"] == PRODUCT_ID
    assert params["publication"] == PUBLICATION_ID
    assert (params["unit"], params["fee"], params["net"]) == (9700, 582, 9118)
    assert params["buyer_sha256"] == hashlib.sha256(b"cognito-sub-1").hexdigest()
    assert "cognito-sub-1" not in json.dumps(params, default=str)


@pytest.mark.asyncio
async def test_venda_sem_comissao_manda_nulos() -> None:
    auth = FakeAuth(script=[linha_gravada()])
    async with client_for(auth) as client:
        resposta = await client.post(
            CRIAR, content=corpo(platform_fee_minor=None, seller_net_minor=None)
        )
    assert resposta.status_code == 201
    _, params = auth.session.calls[0]
    assert (params["fee"], params["net"]) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seller", "subject"),
    [(SELLER_ID, SUJEITO), (None, "stripe-checkout-v1"), (None, "some-user")],
)
async def test_so_o_worker_do_checkout_sem_loja_grava(seller: UUID | None, subject: str) -> None:
    # Pega: um envelope de usuário da loja, ou de outro worker, gravando pedido.
    auth = FakeAuth(script=[], seller_id=seller, subject=subject)
    async with client_for(auth) as client:
        resposta = await client.post(CRIAR, content=corpo())
    assert resposta.status_code == 403
    assert auth.session.calls == []
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mudanca",
    [
        {"unit_amount_minor": "9700"},
        {"unit_amount_minor": 0},
        {"unit_amount_minor": 9.5},
        {"currency": "USD"},
        {"billing_period": "monthly"},
        {"plan_key": "enterprise"},
        {"platform_fee_minor": -1},
        {"buyer_identity_sub": ""},
        {"extra": 1},
    ],
)
async def test_corpo_invalido_e_recusado_antes_do_banco(mudanca: dict[str, Any]) -> None:
    auth = FakeAuth(script=[])
    async with client_for(auth) as client:
        resposta = await client.post(CRIAR, content=corpo(**mudanca))
    assert resposta.status_code == 422
    assert auth.session.calls == []
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
async def test_chave_repetida_no_json_e_recusada() -> None:
    auth = FakeAuth(script=[])
    repetido = corpo()[:-1] + b',"unit_amount_minor":1}'
    async with client_for(auth) as client:
        resposta = await client.post(CRIAR, content=repetido)
    assert resposta.status_code == 422
    assert auth.session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sqlstate", "status", "detalhe"),
    [
        ("P4095", 409, "price_changed"),
        ("42501", 403, "forbidden"),
        ("23514", 422, "invalid_checkout_order"),
        ("08006", 503, "checkout_order_unavailable"),
    ],
)
async def test_recusas_do_banco_viram_respostas_estaveis(
    sqlstate: str, status: int, detalhe: str
) -> None:
    auth = FakeAuth(script=[db_error(sqlstate)])
    async with client_for(auth) as client:
        resposta = await client.post(CRIAR, content=corpo())
    assert resposta.status_code == status
    assert resposta.json() == {"detail": detalhe}


@pytest.mark.asyncio
async def test_ligar_ao_intent_manda_so_o_hash() -> None:
    auth = FakeAuth(script=[True])
    intent = "0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c"
    async with client_for(auth) as client:
        resposta = await client.post(LIGAR, content=json.dumps({"checkout_intent_id": intent}))
    assert resposta.status_code == 204
    assert auth.capabilities == [CAPACIDADE]
    sql, params = auth.session.calls[0]
    assert "app_private.attach_checkout_intent(" in sql
    assert params == {
        "seller": SELLER_ID,
        "order": ORDER_ID,
        "purchase_sha256": hashlib.sha256(intent.encode()).hexdigest(),
    }


@pytest.mark.asyncio
async def test_pedido_que_nao_esta_aberto_responde_409() -> None:
    auth = FakeAuth(script=[False])
    async with client_for(auth) as client:
        resposta = await client.post(LIGAR, content=json.dumps({"checkout_intent_id": "intent-2"}))
    assert resposta.status_code == 409
    assert resposta.json() == {"detail": "checkout_order_not_open"}


@pytest.mark.asyncio
async def test_ligar_com_envelope_de_loja_e_recusado() -> None:
    auth = FakeAuth(script=[], seller_id=SELLER_ID)
    async with client_for(auth) as client:
        resposta = await client.post(LIGAR, content=json.dumps({"checkout_intent_id": "intent"}))
    assert resposta.status_code == 403
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_intent_vazio_e_recusado() -> None:
    auth = FakeAuth(script=[])
    async with client_for(auth) as client:
        resposta = await client.post(LIGAR, content=json.dumps({"checkout_intent_id": ""}))
    assert resposta.status_code == 422
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_o_mesmo_intent_em_dois_pedidos_responde_409() -> None:
    auth = FakeAuth(script=[db_error("23505")])
    async with client_for(auth) as client:
        resposta = await client.post(LIGAR, content=json.dumps({"checkout_intent_id": "intent"}))
    assert resposta.status_code == 409
    assert resposta.json() == {"detail": "checkout_intent_already_attached"}


@pytest.mark.asyncio
async def test_as_rotas_estao_no_app() -> None:
    # Pega: a rota escrita e não incluída no app (404 em produção). Sem a
    # autenticação interna configurada, a rota existente responde 503.
    from appsell_api.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        for caminho in (CRIAR, LIGAR):
            resposta = await client.post(caminho, content=b"{}")
            assert resposta.status_code == 503, caminho
            assert resposta.json() == {"detail": "internal_auth_not_configured"}
