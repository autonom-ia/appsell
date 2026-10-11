from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from appsell_api.storefront_routes import router


class FakeResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeSession:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> FakeResult:
        self.calls.append((str(statement), parameters))
        return FakeResult(self.value)


class FakeAuth:
    def __init__(self, value: Any, seller_id: UUID | None = None) -> None:
        self.session = FakeSession(value)
        self.seller_id = seller_id
        self.capabilities: list[str] = []

    async def authenticate(self, _request: Any, capability: str) -> Any:
        self.capabilities.append(capability)
        return SimpleNamespace(envelope=SimpleNamespace(seller_organization_id=self.seller_id))

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
async def test_storefront_uses_fixed_public_capability_and_bounded_query() -> None:
    payload: dict[str, Any] = {
        "seller": {"slug": "autonomia-demo", "name": "Autonom.ia Demo"},
        "products": [],
        "next_cursor": None,
    }
    auth = FakeAuth(payload)
    async with client_for(auth) as client:
        response = await client.get(
            "/v1/public/storefronts/autonomia-demo?limit=12&cursor=codex-na-pratica"
        )
    assert response.status_code == 200
    assert response.json() == payload
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert auth.capabilities == ["storefront.public.read"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.read_public_storefront" in statement
    assert parameters == {
        "seller_slug": "autonomia-demo",
        "cursor": "codex-na-pratica",
        "limit": 12,
    }


@pytest.mark.asyncio
async def test_product_detail_is_resolved_by_seller_and_product_slug() -> None:
    payload = {
        "seller": {"slug": "autonomia-demo", "name": "Autonom.ia Demo"},
        "product": {"slug": "codex-na-pratica"},
        "other_products": [],
    }
    auth = FakeAuth(payload)
    async with client_for(auth) as client:
        response = await client.get(
            "/v1/public/storefronts/autonomia-demo/products/codex-na-pratica"
        )
    assert response.status_code == 200
    assert response.json() == payload
    statement, parameters = auth.session.calls[0]
    assert "app_private.read_public_product" in statement
    assert parameters == {
        "seller_slug": "autonomia-demo",
        "product_slug": "codex-na-pratica",
    }


@pytest.mark.asyncio
async def test_invalid_slug_fails_before_internal_authentication() -> None:
    auth = FakeAuth({})
    async with client_for(auth) as client:
        response = await client.get("/v1/public/storefronts/Autonomia-Demo")
    assert response.status_code == 422
    assert auth.capabilities == []
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_public_endpoint_rejects_seller_bound_envelope() -> None:
    auth = FakeAuth({}, UUID("92000000-0000-4000-8000-000000000001"))
    async with client_for(auth) as client:
        response = await client.get("/v1/public/storefronts/autonomia-demo")
    assert response.status_code == 401
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_missing_or_non_public_storefront_is_uniform_404() -> None:
    auth = FakeAuth(None)
    async with client_for(auth) as client:
        storefront = await client.get("/v1/public/storefronts/tenant-inexistente")
        product = await client.get(
            "/v1/public/storefronts/tenant-inexistente/products/produto-inexistente"
        )
    assert storefront.status_code == 404
    assert product.status_code == 404
