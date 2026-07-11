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

from appsell_api.learning_routes import _learning_domain_error, router

IDENTITY_ID = UUID("81000000-0000-4000-8000-000000000001")
SELLER_ID = UUID("82000000-0000-4000-8000-000000000001")
ENTITLEMENT_ID = UUID("83000000-0000-4000-8000-000000000001")
LESSON_ID = UUID("84000000-0000-4000-8000-000000000001")
ASSET_ID = UUID("85000000-0000-4000-8000-000000000001")


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
        self.seller_id = seller_id
        self.capabilities: list[str] = []
        self.domain_sessions = 0
        self.request_id = uuid4()

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=IDENTITY_ID),
                seller_organization_id=self.seller_id,
                body_sha256=hashlib.sha256(body).hexdigest(),
                request_id=self.request_id,
            ),
            body=body,
        )

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[FakeSession]:
        self.domain_sessions += 1
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


def progress_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "progress_percent": 40,
        "expected_version": 0,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


def completion_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "expected_version": 1,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.mark.asyncio
async def test_resume_derives_every_scope_from_actor_and_entitlement() -> None:
    player = {"entitlement_id": str(ENTITLEMENT_ID), "selected_lesson_id": str(LESSON_ID)}
    auth = FakeAuth(player)
    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/resume")

    assert response.status_code == 200
    assert response.json() == {"player": player}
    assert auth.capabilities == ["learning.player.read"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.read_learning_player" in statement
    assert parameters == {
        "identity_user_id": IDENTITY_ID,
        "entitlement_id": ENTITLEMENT_ID,
        "lesson_id": None,
    }


@pytest.mark.asyncio
async def test_lesson_read_accepts_no_seller_product_course_or_module_scope() -> None:
    auth = FakeAuth({"selected_lesson_id": str(LESSON_ID)})
    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/lessons/{LESSON_ID}")

    assert response.status_code == 200
    assert auth.capabilities == ["learning.player.read"]
    _, parameters = auth.session.calls[0]
    assert parameters["identity_user_id"] == IDENTITY_ID
    assert parameters["entitlement_id"] == ENTITLEMENT_ID
    assert parameters["lesson_id"] == LESSON_ID
    assert set(parameters) == {"identity_user_id", "entitlement_id", "lesson_id"}


@pytest.mark.asyncio
async def test_progress_binds_signed_hash_request_and_idempotency_to_database_call() -> None:
    progress = {"lesson_progress_percent": 40, "applied": True}
    auth = FakeAuth(progress)
    body = progress_body()
    async with client_for(auth) as client:
        response = await client.put(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/lessons/{LESSON_ID}/progress",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json() == {"progress": progress}
    assert auth.capabilities == ["learning.progress.write"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.record_lesson_progress" in statement
    assert parameters["identity_user_id"] == IDENTITY_ID
    assert parameters["entitlement_id"] == ENTITLEMENT_ID
    assert parameters["lesson_id"] == LESSON_ID
    assert parameters["progress_percent"] == 40
    assert parameters["expected_version"] == 0
    assert isinstance(parameters["idempotency_key"], UUID)
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert parameters["request_id"] == auth.request_id


@pytest.mark.asyncio
async def test_complete_uses_same_narrow_write_capability_and_forces_one_hundred() -> None:
    auth = FakeAuth({"lesson_progress_percent": 100, "applied": True})
    body = completion_body()
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/lessons/{LESSON_ID}/complete",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200
    assert auth.capabilities == ["learning.progress.write"]
    _, parameters = auth.session.calls[0]
    assert parameters["progress_percent"] == 100
    assert parameters["expected_version"] == 1


@pytest.mark.asyncio
async def test_complete_rejects_client_supplied_progress_or_scope_fields() -> None:
    auth = FakeAuth({})
    body = completion_body(progress_percent=100, seller_organization_id=str(SELLER_ID))
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/lessons/{LESSON_ID}/complete",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_missing_or_revoked_player_is_uniformly_not_found() -> None:
    auth = FakeAuth(None)
    async with client_for(auth) as client:
        resume = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/resume")
        lesson = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/lessons/{LESSON_ID}")
    assert resume.status_code == 404
    assert lesson.status_code == 404
    assert resume.json() == lesson.json() == {"detail": "learning_item_unavailable"}


@pytest.mark.asyncio
async def test_non_null_seller_envelope_fails_before_domain_access() -> None:
    auth = FakeAuth({}, seller_id=SELLER_ID)
    async with client_for(auth) as client:
        read = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/resume")
        library = await client.get("/v1/me/library")
        asset = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}")
        write = await client.put(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/lessons/{LESSON_ID}/progress",
            content=progress_body(),
            headers={"content-type": "application/json"},
        )

    assert read.status_code == 401
    assert library.status_code == 401
    assert asset.status_code == 401
    assert write.status_code == 401
    assert auth.domain_sessions == 0
    assert auth.session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "content_type", "expected_status"),
    [
        (progress_body(unexpected="field"), "application/json", 422),
        (progress_body(progress_percent=-1), "application/json", 422),
        (progress_body(progress_percent=101), "application/json", 422),
        (progress_body(expected_version=-1), "application/json", 422),
        (progress_body(idempotency_key="NOT-A-UUID"), "application/json", 422),
        (
            b'{"idempotency_key":"83000000-0000-4000-8000-000000000001",'
            b'"progress_percent":10,"progress_percent":90,"expected_version":0}',
            "application/json",
            422,
        ),
        (progress_body(), "text/plain", 415),
        (b"{" + b'"padding":"' + b"x" * 4_096 + b'"}', "application/json", 413),
    ],
)
async def test_progress_body_is_strict_bounded_and_unambiguous(
    body: bytes, content_type: str, expected_status: int
) -> None:
    auth = FakeAuth({})
    async with client_for(auth) as client:
        response = await client.put(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/lessons/{LESSON_ID}/progress",
            content=body,
            headers={"content-type": content_type},
        )
    assert response.status_code == expected_status
    assert auth.session.calls == []


@pytest.mark.parametrize(
    ("sqlstate", "status_code", "detail"),
    [
        ("28000", 404, "learning_item_unavailable"),
        ("42501", 404, "learning_item_unavailable"),
        ("23505", 409, "idempotency_conflict"),
        ("40001", 409, "progress_version_conflict"),
        ("22023", 422, "invalid_domain_request"),
        ("08006", 503, "learning_unavailable"),
    ],
)
def test_domain_errors_are_stable_and_do_not_create_an_idor_oracle(
    sqlstate: str, status_code: int, detail: str
) -> None:
    error = SimpleNamespace(orig=SimpleNamespace(sqlstate=sqlstate))
    mapped = _learning_domain_error(error)  # type: ignore[arg-type]
    assert mapped.status_code == status_code
    assert mapped.detail == detail
