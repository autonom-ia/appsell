"""Rota que explica ao aluno por que ele não tem acesso (#558).

A rota só lê a função `buyer_access_decision`. Ela nunca libera conteúdo, e
nega por padrão: motivo desconhecido, linha incoerente ou erro viram negação.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError
from test_api_learning import ENTITLEMENT_ID, IDENTITY_ID, SELLER_ID, FakeAuth, client_for

URL = f"/v1/me/learning/items/{ENTITLEMENT_ID}/access"


def row(allowed: bool, reason: str, update_pending: bool = False) -> dict[str, Any]:
    return {"allowed": allowed, "reason": reason, "update_pending": update_pending}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "refunded",
        "revoked",
        "expired",
        "not_started",
        "seller_unavailable",
        "product_unavailable",
        "disputed",
        "subscription_lapsed",
        "unavailable",
    ],
)
async def test_each_denial_reason_reaches_the_student(reason: str) -> None:
    auth = FakeAuth(row(False, reason))
    async with client_for(auth) as client:
        response = await client.get(URL)

    assert response.status_code == 200
    assert response.json() == {
        "access": {"allowed": False, "reason": reason, "update_pending": False}
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_active_access_carries_the_pending_update_flag() -> None:
    auth = FakeAuth(row(True, "active", True))
    async with client_for(auth) as client:
        response = await client.get(URL)

    assert response.json() == {
        "access": {"allowed": True, "reason": "active", "update_pending": True}
    }


@pytest.mark.asyncio
async def test_not_found_is_the_same_404_as_every_content_route() -> None:
    auth = FakeAuth(row(False, "not_found"))
    async with client_for(auth) as client:
        response = await client.get(URL)

    assert response.status_code == 404
    assert response.json() == {"detail": "learning_item_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        row(False, "um motivo que ninguém previu"),
        row(True, "refunded"),
        row(True, "um motivo que ninguém previu"),
        {"allowed": "yes", "reason": "active", "update_pending": False},
        {"allowed": True, "reason": None, "update_pending": False},
    ],
)
async def test_anything_unexpected_is_denied(value: dict[str, Any]) -> None:
    auth = FakeAuth(value)
    async with client_for(auth) as client:
        response = await client.get(URL)

    assert response.status_code == 200
    assert response.json()["access"]["allowed"] is False
    assert response.json()["access"]["reason"] == "unavailable"


@pytest.mark.asyncio
async def test_uses_the_library_capability_and_the_buyer_identity() -> None:
    auth = FakeAuth(row(False, "refunded"))
    async with client_for(auth) as client:
        await client.get(URL)

    assert auth.capabilities == ["library.read"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.buyer_access_decision(" in statement
    assert parameters == {"identity_user_id": IDENTITY_ID, "entitlement_id": ENTITLEMENT_ID}


@pytest.mark.asyncio
async def test_seller_envelope_is_refused_before_the_database() -> None:
    auth = FakeAuth(row(True, "active"), seller_id=SELLER_ID)
    async with client_for(auth) as client:
        response = await client.get(URL)

    assert response.status_code == 401
    assert auth.domain_sessions == 0


class Sqlstate(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


class FailingSession:
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        self.calls: list[Any] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> Any:
        raise DBAPIError("select", parameters, Sqlstate(self.sqlstate))


@pytest.mark.asyncio
async def test_context_refusal_is_the_uniform_404() -> None:
    auth = FakeAuth(None)
    auth.session = FailingSession("28000")  # type: ignore[assignment]
    async with client_for(auth) as client:
        response = await client.get(URL)

    assert response.status_code == 404
    assert response.json() == {"detail": "learning_item_unavailable"}


@pytest.mark.asyncio
async def test_unknown_database_error_is_not_a_grant() -> None:
    auth = FakeAuth(None)
    auth.session = FailingSession("XX000")  # type: ignore[assignment]
    async with client_for(auth) as client:
        response = await client.get(URL)

    assert response.status_code == 503
    assert "access" not in response.json()
