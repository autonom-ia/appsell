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

from appsell_api.platform_routes import router

ACTOR_ID = UUID("91000000-0000-4000-8000-000000000019")
TENANT_ID = UUID("92000000-0000-4000-8000-000000000019")


class FakeSession:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, statement: Any, parameters: dict[str, Any]) -> Any:
        self.calls.append((str(statement), parameters))
        return self.result


class FakeAuth:
    def __init__(self, result: Any, *, seller_id: UUID | None = None) -> None:
        self.session = FakeSession(result)
        self.capabilities: list[str] = []
        self.rejected = 0
        self.request_id = uuid4()
        self.seller_id = seller_id

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=ACTOR_ID, sub="platform-operator"),
                seller_organization_id=self.seller_id,
                body_sha256=hashlib.sha256(body).hexdigest(),
                request_id=self.request_id,
            ),
            body=body,
            consume_rejected_request=self.consume_rejected_request,
        )

    async def consume_rejected_request(self) -> None:
        self.rejected += 1

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[FakeSession]:
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected_status", "capability"),
    [
        ("suspend", "suspended", "platform.tenant.suspend"),
        ("reactivate", "active", "platform.tenant.reactivate"),
    ],
)
async def test_platform_tenant_status_route_binds_signed_actor_and_target(
    operation: str, expected_status: str, capability: str
) -> None:
    result = {"id": str(TENANT_ID), "status": expected_status, "applied": True}
    auth = FakeAuth(result)
    body = json.dumps({"reason": "Decisao operacional auditada"}, separators=(",", ":")).encode()
    path = f"/v1/platform/tenants/{TENANT_ID}/{operation}"
    async with client_for(auth) as client:
        response = await client.post(
            path, content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 200
    assert response.json() == result
    assert auth.capabilities == [capability]
    statement, parameters = auth.session.calls[0]
    assert "app_private.set_tenant_status" in statement
    assert parameters == {
        "identity_user_id": ACTOR_ID,
        "identity_sub": "platform-operator",
        "tenant_id": TENANT_ID,
        "target_status": expected_status,
        "reason": "Decisao operacional auditada",
        "request_id": auth.request_id,
    }


@pytest.mark.asyncio
async def test_platform_tenant_status_rejects_tenant_context_and_invalid_body_before_db() -> None:
    tenant_context = FakeAuth({}, seller_id=TENANT_ID)
    path = f"/v1/platform/tenants/{TENANT_ID}/suspend"
    async with client_for(tenant_context) as client:
        rejected_context = await client.post(path, json={"reason": "Suspensao administrativa"})
    assert rejected_context.status_code == 401
    assert tenant_context.rejected == 1
    assert tenant_context.session.calls == []

    invalid = FakeAuth({})
    duplicate = b'{"reason":"Motivo suficiente","reason":"Motivo duplicado"}'
    async with client_for(invalid) as client:
        rejected_body = await client.post(
            path, content=duplicate, headers={"content-type": "application/json"}
        )
    assert rejected_body.status_code == 422
    assert invalid.rejected == 1
    assert invalid.session.calls == []


@pytest.mark.asyncio
async def test_platform_tenant_status_hides_unknown_tenant() -> None:
    auth = FakeAuth(None)
    path = f"/v1/platform/tenants/{TENANT_ID}/reactivate"
    async with client_for(auth) as client:
        response = await client.post(path, json={"reason": "Tenant inexistente no cadastro"})
    assert response.status_code == 404
    assert response.json() == {"detail": "tenant_not_found"}
