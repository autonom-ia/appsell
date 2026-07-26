"""Route contract of the provider-driven payout status sync endpoint.

Unit tests over a scripted session: the route must pin the worker identity
(no seller, fixed subject) BEFORE any SQL, map the body one-to-one onto the
sync function, and acknowledge unknown accounts without error so the provider
stops redelivering.
"""

from __future__ import annotations

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

from appsell_api.payout_sync_routes import router

SELLER_ID = UUID("63000000-0000-4000-8000-000000000001")
WORKER_ID = UUID("63000000-0000-4000-8000-000000000002")
ACCOUNT = "acct_1SyncTest"
SYNC_SUBJECT = "stripe-connect-webhook-v1"


class FakeDbError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class ScriptedSession:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        self.calls.append((str(statement), parameters or {}))
        assert self.script, "route issued more SQL than the test scripted"
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class FakeAuth:
    def __init__(
        self,
        *,
        script: list[Any],
        seller_id: UUID | None = None,
        subject: str = SYNC_SUBJECT,
    ) -> None:
        self.session = ScriptedSession(script)
        self.seller_id = seller_id
        self.subject = subject
        self.capabilities: list[str] = []
        self.rejected_requests = 0
        self.request_id = uuid4()

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=WORKER_ID, sub=self.subject),
                seller_organization_id=self.seller_id,
                request_id=self.request_id,
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


def sync_body(**overrides: Any) -> bytes:
    value = {
        "provider": "stripe",
        "provider_account_id": ACCOUNT,
        "charges_enabled": True,
        "payouts_enabled": True,
        "details_submitted": True,
        "disabled_reason": None,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.mark.asyncio
async def test_sync_maps_the_verified_status_onto_the_database_call() -> None:
    payout_account_id = uuid4()
    auth = FakeAuth(script=[payout_account_id])
    async with client_for(auth) as client:
        response = await client.post("/v1/internal/payout-accounts/sync", content=sync_body())
    assert response.status_code == 200
    assert response.json() == {
        "updated": True,
        "payout_account_id": str(payout_account_id),
    }
    assert auth.capabilities == ["commerce.payout.sync"]
    sql, params = auth.session.calls[0]
    assert "sync_payout_account_status" in sql
    assert params["provider_account_id"] == ACCOUNT
    assert params["charges_enabled"] is True
    assert params["request_id"] == auth.request_id


@pytest.mark.asyncio
async def test_unknown_account_is_acknowledged_without_error() -> None:
    # A non-2xx would make the provider redeliver for days over an account this
    # platform never bound.
    auth = FakeAuth(script=[None])
    async with client_for(auth) as client:
        response = await client.post("/v1/internal/payout-accounts/sync", content=sync_body())
    assert response.status_code == 200
    assert response.json() == {"updated": False}


@pytest.mark.asyncio
async def test_sync_refuses_a_tenant_scoped_envelope_before_any_sql() -> None:
    # A user-shaped envelope (it carries a seller) must never drive the
    # tenant-less sync path, whatever capability it claims.
    auth = FakeAuth(script=[], seller_id=SELLER_ID)
    async with client_for(auth) as client:
        response = await client.post("/v1/internal/payout-accounts/sync", content=sync_body())
    assert response.status_code == 403
    assert auth.rejected_requests == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_sync_refuses_a_foreign_worker_subject() -> None:
    auth = FakeAuth(script=[], subject="fulfillment-worker-v1")
    async with client_for(auth) as client:
        response = await client.post("/v1/internal/payout-accounts/sync", content=sync_body())
    assert response.status_code == 403
    assert auth.rejected_requests == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_sync_rejects_a_malformed_provider_account_id() -> None:
    auth = FakeAuth(script=[])
    async with client_for(auth) as client:
        response = await client.post(
            "/v1/internal/payout-accounts/sync",
            content=sync_body(provider_account_id="not-an-account"),
        )
    assert response.status_code == 422
    assert auth.rejected_requests == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_database_denial_maps_to_forbidden() -> None:
    auth = FakeAuth(script=[DBAPIError("select 1", {}, FakeDbError("42501"))])
    async with client_for(auth) as client:
        response = await client.post("/v1/internal/payout-accounts/sync", content=sync_body())
    assert response.status_code == 403
