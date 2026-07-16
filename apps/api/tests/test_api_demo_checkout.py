from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from appsell_api.checkout_routes import router

IDENTITY = UUID("91000000-0000-4000-8000-000000000001")


class FakeSession:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, statement: Any, parameters: dict[str, Any]) -> Any:
        self.calls.append((str(statement), parameters))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeAuth:
    def __init__(self, result: Any) -> None:
        self.session = FakeSession(result)
        self.capabilities: list[str] = []
        self.rejected = 0
        self.request_id = uuid4()

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=IDENTITY),
                body_sha256=hashlib.sha256(body).hexdigest(),
                request_id=self.request_id,
            ),
            body=body,
            consume_rejected_request=self.consume_rejected,
        )

    async def consume_rejected(self) -> None:
        self.rejected += 1

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[FakeSession]:
        yield self.session


@asynccontextmanager
async def client_for(
    auth: FakeAuth, *, environment: str = "ci"
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    app.state.settings = SimpleNamespace(environment=environment)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_quote_derives_actor_without_tenant_input() -> None:
    quote = {"schema_version": 1, "already_owned": False}
    auth = FakeAuth(quote)
    async with client_for(auth) as client:
        response = await client.get("/v1/me/demo-checkout")
    assert response.status_code == 200
    assert response.json() == {"checkout": quote}
    assert auth.capabilities == ["commerce.demo.checkout.read"]
    statement, parameters = auth.session.calls[0]
    assert "read_demo_checkout" in statement
    assert parameters == {"actor": IDENTITY}


@pytest.mark.asyncio
async def test_complete_hashes_coupon_and_uses_signed_body_receipt() -> None:
    result = {"schema_version": 1, "status": "processing", "provider_called": False}
    auth = FakeAuth(result)
    key = uuid4()
    body = json.dumps(
        {"idempotency_key": str(key), "coupon_code": "AUTONOMIA100"},
        separators=(",", ":"),
    ).encode()
    async with client_for(auth) as client:
        response = await client.post(
            "/v1/me/demo-checkout", content=body, headers={"content-type": "application/json"}
        )
    assert response.status_code == 200
    assert response.json() == {"checkout": result}
    assert auth.capabilities == ["commerce.demo.checkout.complete"]
    statement, parameters = auth.session.calls[0]
    assert "complete_demo_zero_checkout" in statement
    assert parameters == {
        "actor": IDENTITY,
        "coupon_sha256": hashlib.sha256(b"AUTONOMIA100").hexdigest(),
        "idempotency": key,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "request_id": auth.request_id,
    }


@pytest.mark.asyncio
async def test_invalid_coupon_body_is_consumed_without_database_call() -> None:
    auth = FakeAuth({})
    async with client_for(auth) as client:
        response = await client.post(
            "/v1/me/demo-checkout",
            json={"idempotency_key": str(uuid4()), "coupon_code": " cupom "},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request_body"}
    assert auth.rejected == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_wrong_well_formed_coupon_has_checkout_specific_contract() -> None:
    original = SimpleNamespace(sqlstate="28000")
    auth = FakeAuth(DBAPIError("checkout", {}, original, False))
    async with client_for(auth) as client:
        response = await client.post(
            "/v1/me/demo-checkout",
            json={"idempotency_key": str(uuid4()), "coupon_code": "ERRADO100"},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_demo_coupon"}


@pytest.mark.asyncio
async def test_checkout_is_hidden_outside_local_ci() -> None:
    auth = FakeAuth({})
    async with client_for(auth, environment="production") as client:
        response = await client.get("/v1/me/demo-checkout")
    assert response.status_code == 404
    assert response.json() == {"detail": "demo_checkout_unavailable"}
    assert auth.session.calls == []
