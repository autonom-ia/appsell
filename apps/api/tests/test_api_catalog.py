from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
from fastapi import FastAPI

from appsell_api.catalog_routes import _domain_error, router
from appsell_api.config import get_settings
from appsell_api.main import app as domain_app

SELLER_ID = UUID("52000000-0000-4000-8000-000000000001")
OTHER_SELLER_ID = UUID("52000000-0000-4000-8000-000000000002")
IDENTITY_ID = UUID("51000000-0000-4000-8000-000000000001")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")


class FakeResult:
    def __init__(
        self, *, one: dict[str, Any] | None = None, all: list[dict[str, Any]] | None = None
    ):
        self._one = one
        self._all = all or []

    def mappings(self) -> FakeResult:
        return self

    def one(self) -> dict[str, Any]:
        assert self._one is not None
        return self._one

    def all(self) -> list[dict[str, Any]]:
        return self._all


class FakeSession:
    def __init__(self, result: FakeResult):
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> FakeResult:
        self.calls.append((str(statement), parameters))
        return self.result


class FakeAuth:
    def __init__(
        self,
        *,
        seller_id: UUID | None,
        result: FakeResult,
    ) -> None:
        self.seller_id = seller_id
        self.session = FakeSession(result)
        self.capabilities: list[str] = []
        self.domain_sessions = 0

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=IDENTITY_ID),
                seller_organization_id=self.seller_id,
                body_sha256=hashlib.sha256(body).hexdigest(),
                request_id=uuid4(),
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


def draft_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "slug": "produto-inicial",
        "title": "Produto Inicial",
        "description": "Rascunho privado",
        "course_title": "Curso Inicial",
        **overrides,
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.mark.asyncio
async def test_create_draft_binds_seller_actor_hash_and_idempotency() -> None:
    product_id, course_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "catalog_product_id": product_id,
                "course_id": course_id,
                "created": True,
            }
        ),
    )
    body = draft_body()

    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 201
    assert response.json() == {
        "product_id": str(product_id),
        "course_id": str(course_id),
        "status": "draft",
        "created": True,
    }
    assert auth.capabilities == ["catalog.draft.create"]
    assert auth.domain_sessions == 1
    statement, parameters = auth.session.calls[0]
    assert "app_private.create_product_course_draft" in statement
    assert parameters["identity_user_id"] == IDENTITY_ID
    assert parameters["seller_id"] == SELLER_ID
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert isinstance(parameters["idempotency_key"], UUID)


@pytest.mark.asyncio
async def test_create_idempotent_replay_returns_200() -> None:
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "catalog_product_id": uuid4(),
                "course_id": uuid4(),
                "created": False,
            }
        ),
    )
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=draft_body(),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200
    assert response.json()["created"] is False


@pytest.mark.asyncio
async def test_envelope_seller_must_match_path_before_domain_access() -> None:
    auth = FakeAuth(seller_id=OTHER_SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        create = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=draft_body(),
            headers={"content-type": "application/json"},
        )
        listing = await client.get(f"/v1/me/sellers/{SELLER_ID}/products")
    assert create.status_code == 401
    assert listing.status_code == 401
    assert auth.domain_sessions == 0
    assert auth.session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        draft_body(idempotency_key="not-a-uuid"),
        draft_body(slug="UPPERCASE"),
        draft_body(title=" título com espaço"),
        draft_body(course_title="x"),
        draft_body(description="a" * 2001),
        draft_body(unexpected="field"),
        b'{"idempotency_key":"52000000-0000-4000-8000-000000000001",'
        b'"slug":"primeiro","slug":"segundo","title":"Produto",'
        b'"description":"","course_title":"Curso"}',
    ],
)
async def test_invalid_or_ambiguous_create_body_is_rejected(body: bytes) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request_body"}
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_list_drafts_is_tenant_scoped_and_uses_bounded_keyset() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    product_ids = [uuid4(), uuid4(), uuid4()]
    rows = [
        {
            "id": product_id,
            "slug": f"produto-{index}",
            "title": f"Produto {index}",
            "description": "Privado",
            "status": "draft",
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "course_id": uuid4(),
            "course_title": f"Curso {index}",
            "course_status": "draft",
            "course_draft_version": 1,
        }
        for index, product_id in enumerate(product_ids)
    ]
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult(all=rows))
    cursor = uuid4()

    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/sellers/{SELLER_ID}/products?limit=2&cursor={cursor}")

    assert response.status_code == 200
    payload = response.json()
    assert [product["id"] for product in payload["products"]] == [
        str(product_ids[0]),
        str(product_ids[1]),
    ]
    assert payload["next_cursor"] == str(product_ids[1])
    assert payload["products"][0]["course"]["status"] == "draft"
    assert auth.capabilities == ["catalog.draft.read"]
    statement, parameters = auth.session.calls[0]
    assert "product.status = 'draft'" in statement
    assert "course.status = 'draft'" in statement
    assert "order by product.created_at desc, product.id desc" in statement
    assert parameters == {"seller_id": SELLER_ID, "cursor": cursor, "fetch_limit": 3}


@pytest.mark.asyncio
async def test_list_limit_and_cursor_are_validated_before_authentication() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        too_large = await client.get(f"/v1/me/sellers/{SELLER_ID}/products?limit=101")
        invalid_cursor = await client.get(f"/v1/me/sellers/{SELLER_ID}/products?cursor=not-a-uuid")
    assert too_large.status_code == 422
    assert invalid_cursor.status_code == 422
    assert auth.capabilities == []


def test_domain_errors_are_stable_and_do_not_leak_driver_details() -> None:
    class DriverError:
        def __init__(self, sqlstate: str | None):
            self.sqlstate = sqlstate

    for sqlstate, expected_status, expected_detail in [
        ("23505", 409, "resource_conflict"),
        ("22023", 422, "invalid_domain_request"),
        ("28000", 403, "forbidden"),
        ("42501", 403, "forbidden"),
        (None, 503, "domain_unavailable"),
    ]:
        mapped = _domain_error(SimpleNamespace(orig=DriverError(sqlstate)))  # type: ignore[arg-type]
        assert mapped.status_code == expected_status
        assert mapped.detail == expected_detail
        assert "driver" not in str(mapped.detail)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not all((RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)
async def test_signed_api_creates_replays_and_lists_persistent_draft() -> None:
    assert RUNTIME_URL and CONTEXT_KEY_HEX
    internal_key = bytes(range(32, 64))
    service_credential = bytes(range(96, 128))
    key_id = "catalog-api-v1"
    issuer = "appsell-catalog-bff-test"
    audience = "appsell-catalog-api-test"
    identity_issuer = "https://identity.catalog-api.test.autonomia.site"
    subject = f"catalog-api-user-{uuid4()}"
    identity_id = uuid5(NAMESPACE_URL, f"{identity_issuer}\0{subject}")
    session_id = f"catalog-api-session-{uuid4()}"
    environment = {
        "APPSELL_API_ENVIRONMENT": "ci",
        "APPSELL_API_DATABASE_URL": RUNTIME_URL.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        ),
        "APPSELL_API_INTERNAL_ISSUER": issuer,
        "APPSELL_API_INTERNAL_AUDIENCE": audience,
        "APPSELL_API_IDENTITY_ISSUER": identity_issuer,
        "APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON": json.dumps(
            [
                {
                    "id": key_id,
                    "key": base64.b64encode(internal_key).decode(),
                    "active": True,
                }
            ]
        ),
        "APPSELL_API_CONTEXT_HMAC_KEY_B64": base64.b64encode(
            bytes.fromhex(CONTEXT_KEY_HEX)
        ).decode(),
        "APPSELL_API_SERVICE_CREDENTIAL_B64": base64.b64encode(service_credential).decode(),
    }
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    get_settings.cache_clear()

    def headers(
        *,
        method: str,
        path: str,
        capability: str,
        body: bytes = b"",
        seller_id: UUID | None = None,
    ) -> dict[str, str]:
        now = int(time.time())
        payload = {
            "version": 1,
            "kid": key_id,
            "issuer": issuer,
            "audience": audience,
            "session_ref": hashlib.sha256(
                b"appsell.session-ref.v1\n" + session_id.encode()
            ).hexdigest(),
            "session_epoch": 1,
            "identity": {
                "issuer": identity_issuer,
                "sub": subject,
                "user_id": str(identity_id),
            },
            "capability": capability,
            "seller_organization_id": str(seller_id) if seller_id else None,
            "method": method,
            "path": path,
            "query_sha256": hashlib.sha256(b"").hexdigest(),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "request_id": str(uuid4()),
            "iat": now,
            "exp": now + 30,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=")
        signature = base64.urlsafe_b64encode(
            hmac.digest(internal_key, b"appsell.internal.v1." + encoded, "sha256")
        ).rstrip(b"=")
        return {
            "x-appsell-service-credential": base64.b64encode(service_credential).decode(),
            "x-appsell-internal-envelope": f"v1.{encoded.decode()}.{signature.decode()}",
            "content-type": "application/json",
        }

    try:
        async with domain_app.router.lifespan_context(domain_app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=domain_app), base_url="http://api.test"
            ) as client:
                sync_body = json.dumps(
                    {"email": f"catalog-{uuid4().hex}@example.test", "email_verified": True},
                    separators=(",", ":"),
                ).encode()
                synced = await client.put(
                    "/v1/me",
                    content=sync_body,
                    headers=headers(
                        method="PUT",
                        path="/v1/me",
                        capability="identity.sync",
                        body=sync_body,
                    ),
                )
                assert synced.status_code == 200

                seller_body = json.dumps(
                    {
                        "idempotency_key": str(uuid4()),
                        "slug": f"catalog-api-{uuid4().hex[:12]}",
                        "display_name": "Catalog API Test",
                    },
                    separators=(",", ":"),
                ).encode()
                onboarded = await client.post(
                    "/v1/me/sellers",
                    content=seller_body,
                    headers=headers(
                        method="POST",
                        path="/v1/me/sellers",
                        capability="seller.onboard",
                        body=seller_body,
                    ),
                )
                assert onboarded.status_code == 201
                seller_id = UUID(onboarded.json()["id"])
                path = f"/v1/me/sellers/{seller_id}/products"
                product_body = draft_body(slug=f"produto-{uuid4().hex[:12]}")
                idempotency_key = json.loads(product_body)["idempotency_key"]

                created = await client.post(
                    path,
                    content=product_body,
                    headers=headers(
                        method="POST",
                        path=path,
                        capability="catalog.draft.create",
                        body=product_body,
                        seller_id=seller_id,
                    ),
                )
                assert created.status_code == 201
                assert created.json()["status"] == "draft"

                replay_body = json.loads(product_body)
                replay_body["idempotency_key"] = idempotency_key
                replay_bytes = json.dumps(replay_body, separators=(",", ":")).encode()
                replayed = await client.post(
                    path,
                    content=replay_bytes,
                    headers=headers(
                        method="POST",
                        path=path,
                        capability="catalog.draft.create",
                        body=replay_bytes,
                        seller_id=seller_id,
                    ),
                )
                assert replayed.status_code == 200
                assert replayed.json() == {**created.json(), "created": False}

                listed = await client.get(
                    path,
                    headers=headers(
                        method="GET",
                        path=path,
                        capability="catalog.draft.read",
                        seller_id=seller_id,
                    ),
                )
                assert listed.status_code == 200
                assert listed.json()["products"][0]["id"] == created.json()["product_id"]
                assert listed.json()["products"][0]["status"] == "draft"
                assert listed.json()["products"][0]["course"]["status"] == "draft"
    finally:
        get_settings.cache_clear()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
