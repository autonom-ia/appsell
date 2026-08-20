"""Route contract of the payout-account binding endpoints.

These are unit tests over a scripted session: they prove the ROUTE's own
guarantees — membership is checked before any payout SQL runs, the denied
re-point is audited and the audit is issued OUTSIDE the failed savepoint, and
SQLSTATEs map to the intended HTTP responses. The database-side guarantees are
proven in test_postgres_seller_payout_account.py and
test_postgres_payout_repoint_audit.py.
"""

from __future__ import annotations

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

from appsell_api.payout_routes import router

SELLER_ID = UUID("62000000-0000-4000-8000-000000000001")
OTHER_SELLER_ID = UUID("62000000-0000-4000-8000-000000000002")
IDENTITY_ID = UUID("61000000-0000-4000-8000-000000000001")
ACCOUNT = "acct_1TestPayout"


class FakeDbError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("select 1", {}, FakeDbError(sqlstate))


class FakeResult:
    def __init__(self, *, first: Any = None, one_or_none: Any = None) -> None:
        self._first = first
        self._one_or_none = one_or_none

    def first(self) -> Any:
        return self._first

    def mappings(self) -> FakeResult:
        return self

    def one_or_none(self) -> Any:
        return self._one_or_none


class FakeNested:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.is_active = True

    async def commit(self) -> None:
        self.committed = True
        self.is_active = False

    async def rollback(self) -> None:
        self.rolled_back = True
        self.is_active = False


class ScriptedSession:
    """Feeds one scripted response (or exception) per SQL call, in order."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.nested: list[FakeNested] = []

    def _next(self, statement: Any, parameters: dict[str, Any] | None) -> Any:
        self.calls.append((str(statement), parameters or {}))
        assert self.script, "route issued more SQL than the test scripted"
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return self._next(statement, parameters)

    async def scalar(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return self._next(statement, parameters)

    async def begin_nested(self) -> FakeNested:
        nested = FakeNested()
        self.nested.append(nested)
        return nested


class FakeAuth:
    def __init__(self, *, seller_id: UUID | None, script: list[Any]) -> None:
        self.seller_id = seller_id
        self.session = ScriptedSession(script)
        self.capabilities: list[str] = []
        self.rejected_requests = 0
        self.request_id = uuid4()

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=IDENTITY_ID),
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


def bind_body(**overrides: Any) -> bytes:
    value = {
        "provider": "stripe",
        "provider_account_id": ACCOUNT,
        "charges_enabled": False,
        "payouts_enabled": False,
        "details_submitted": False,
        "disabled_reason": None,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


def member() -> str:
    return "seller_owner"


def not_a_member() -> str | None:
    return None


@pytest.mark.asyncio
async def test_bind_checks_membership_then_upserts_with_the_envelope_request_id() -> None:
    payout_account_id = uuid4()
    auth = FakeAuth(seller_id=SELLER_ID, script=[member(), payout_account_id])
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/payout-account", content=bind_body()
        )
    assert response.status_code == 200
    assert response.json()["payout_account_id"] == str(payout_account_id)
    assert auth.capabilities == ["commerce.payout.manage"]
    membership_sql, membership_params = auth.session.calls[0]
    assert "resolve_seller_membership_role" in membership_sql
    assert membership_params["seller_id"] == SELLER_ID
    upsert_sql, upsert_params = auth.session.calls[1]
    assert "upsert_seller_payout_account" in upsert_sql
    assert upsert_params["provider_account_id"] == ACCOUNT
    assert upsert_params["request_id"] == auth.request_id
    assert auth.session.nested[0].committed is True


@pytest.mark.asyncio
async def test_bind_refuses_a_non_owner_before_touching_payout_sql() -> None:
    # Membership is the route's own gate: the 0021 function does not check it,
    # so a route that skips this select would let any authenticated user whose
    # envelope echoes the right seller id reach the payout upsert.
    auth = FakeAuth(seller_id=SELLER_ID, script=[not_a_member()])
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/payout-account", content=bind_body()
        )
    assert response.status_code == 403
    assert len(auth.session.calls) == 1
    assert auth.session.nested == []


@pytest.mark.asyncio
async def test_bind_rejects_an_envelope_for_a_different_seller() -> None:
    auth = FakeAuth(seller_id=OTHER_SELLER_ID, script=[])
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/payout-account", content=bind_body()
        )
    assert response.status_code == 401
    assert auth.rejected_requests == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_denied_repoint_is_audited_outside_the_failed_savepoint() -> None:
    # The savepoint rolls back the refused upsert; the audit call must come
    # AFTER that rollback (or it would be undone with it) and the route must
    # still exit the session normally so the audit commits before the 409.
    audit_id = uuid4()
    auth = FakeAuth(seller_id=SELLER_ID, script=[member(), db_error("55000"), audit_id])
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/payout-account", content=bind_body()
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "payout_account_already_bound"
    assert auth.session.nested[0].rolled_back is True
    audit_sql, audit_params = auth.session.calls[2]
    assert "record_payout_repoint_denied" in audit_sql
    assert audit_params["attempted"] == ACCOUNT
    assert audit_params["request_id"] == auth.request_id


@pytest.mark.asyncio
async def test_other_database_errors_do_not_write_denial_audit() -> None:
    # A unique-constraint race (23505) is not a re-point denial; recording it
    # as one would poison the audit trail with false accusations.
    auth = FakeAuth(seller_id=SELLER_ID, script=[member(), db_error("23505")])
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/payout-account", content=bind_body()
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "payout_account_conflict"
    assert len(auth.session.calls) == 2


@pytest.mark.asyncio
async def test_bind_rejects_a_malformed_provider_account_id() -> None:
    # The BFF only ever sends ids from an authenticated accounts.create
    # response; anything else reaching this route is a bug or an attack.
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/payout-account",
            content=bind_body(provider_account_id="not-an-account'; drop table--"),
        )
    assert response.status_code == 422
    assert auth.rejected_requests == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_read_returns_the_cached_binding_for_a_member() -> None:
    synced = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    auth = FakeAuth(
        seller_id=SELLER_ID,
        script=[
            member(),
            FakeResult(
                one_or_none={
                    "payout_account_id": uuid4(),
                    "provider_account_id": ACCOUNT,
                    "charges_enabled": True,
                    "payouts_enabled": False,
                    "details_submitted": True,
                    "disabled_reason": None,
                    "status_synced_at": synced,
                }
            ),
        ],
    )
    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/sellers/{SELLER_ID}/payout-account")
    assert response.status_code == 200
    payload = response.json()["payout_account"]
    assert payload["provider_account_id"] == ACCOUNT
    assert payload["charges_enabled"] is True
    assert payload["payouts_enabled"] is False
    assert payload["status_synced_at"] == synced.isoformat()
    assert auth.capabilities == ["commerce.payout.read"]
    _, membership_params = auth.session.calls[0]
    assert membership_params["seller_id"] == SELLER_ID


@pytest.mark.asyncio
async def test_read_returns_null_when_no_account_is_bound() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[member(), FakeResult(one_or_none=None)])
    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/sellers/{SELLER_ID}/payout-account")
    assert response.status_code == 200
    assert response.json() == {"payout_account": None}


@pytest.mark.asyncio
async def test_read_refuses_an_unsupported_provider() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    async with client_for(auth) as client:
        response = await client.get(
            f"/v1/me/sellers/{SELLER_ID}/payout-account", params={"provider": "paypal"}
        )
    assert response.status_code == 422
    assert auth.rejected_requests == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_read_refuses_a_non_member() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[not_a_member()])
    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/sellers/{SELLER_ID}/payout-account")
    assert response.status_code == 403
    assert len(auth.session.calls) == 1
