from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from appsell_api.asset_routes import router
from appsell_api.internal_requests import AuthenticatedInternalRequest
from appsell_api.service_auth import ServiceEnvelope, ServiceIdentity
from appsell_api.storage_s3 import PresignedRequest

SELLER = UUID("52000000-0000-4000-8000-000000000001")
OTHER_SELLER = UUID("52000000-0000-4000-8000-000000000002")
PRODUCT = UUID("53000000-0000-4000-8000-000000000001")
COURSE = UUID("54000000-0000-4000-8000-000000000001")
MODULE = UUID("55000000-0000-4000-8000-000000000001")
LESSON = UUID("56000000-0000-4000-8000-000000000001")
ACTOR = UUID("51000000-0000-4000-8000-000000000001")


class FakeResult:
    def __init__(self, value: Any):
        self.value = value

    def mappings(self) -> FakeResult:
        return self

    def one(self) -> Any:
        return self.value


class FakeSession:
    def __init__(self, scalar_values: list[Any], execute_values: list[Any] | None = None):
        self.scalar_values = scalar_values
        self.execute_values = execute_values or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, statement: Any, parameters: dict[str, Any]) -> Any:
        self.calls.append((str(statement), parameters))
        return self.scalar_values.pop(0)

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> FakeResult:
        self.calls.append((str(statement), parameters))
        return FakeResult(self.execute_values.pop(0))


class FakeAuth:
    def __init__(
        self,
        *,
        seller: UUID | None,
        scalar_values: list[Any],
        execute_values: list[Any] | None = None,
    ):
        self.seller = seller
        self.session = FakeSession(scalar_values, execute_values)
        self.authenticated_capabilities: list[str] = []
        self.context_capabilities: list[str] = []

    async def authenticate(self, request: Any, capability: str) -> AuthenticatedInternalRequest:
        body = await request.body()
        self.authenticated_capabilities.append(capability)
        now = int(time.time())
        return AuthenticatedInternalRequest(
            envelope=ServiceEnvelope(
                version=1,
                kid="test-v1",
                issuer="appsell-bff",
                audience="appsell-api",
                session_ref="test-session",
                session_epoch=1,
                identity=ServiceIdentity(
                    issuer="https://identity.test.autonomia.site",
                    sub="asset-admin",
                    user_id=ACTOR,
                ),
                capability=capability,
                seller_organization_id=self.seller,
                method=request.method,
                path=request.url.path,
                query_sha256=hashlib.sha256(b"").hexdigest(),
                body_sha256=hashlib.sha256(body).hexdigest(),
                request_id=uuid4(),
                iat=now,
                exp=now + 60,
            ),
            body=body,
        )

    @asynccontextmanager
    async def domain_session(self, envelope: ServiceEnvelope) -> AsyncIterator[FakeSession]:
        self.context_capabilities.append(envelope.capability)
        yield self.session


class FakeStorage:
    def __init__(self, upload_id: UUID):
        self.upload_id = upload_id
        self.presign_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.observed_upload_id = str(upload_id)

    def presign_put(self, **kwargs: Any) -> PresignedRequest:
        self.presign_calls.append(kwargs)
        return PresignedRequest(
            url="http://127.0.0.1:8333/quarantine/key?X-Amz-Signature=signed",
            headers={
                "content-type": "application/pdf",
                "x-amz-checksum-sha256": "checksum",
                "x-amz-meta-appsell-upload-id": str(self.upload_id),
            },
            expires_in_seconds=300,
            required_content_length=128,
        )

    def head(self, **kwargs: Any) -> Any:
        self.head_calls.append(kwargs)
        return SimpleNamespace(
            appsell_upload_id=self.observed_upload_id,
            version_id="source-v1",
            size=128,
            checksum_sha256_b64=base64.b64encode(bytes.fromhex("ab" * 32)).decode(),
            etag="etag-v1",
        )


def asset_path(suffix: str = "") -> str:
    return (
        f"/v1/me/sellers/{SELLER}/products/{PRODUCT}/courses/{COURSE}"
        f"/modules/{MODULE}/lessons/{LESSON}/assets{suffix}"
    )


def intent_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "expected_course_version": 1,
        "title": "Material seguro",
        "file_name": "material.pdf",
        "media_type": "application/pdf",
        "byte_size": 128,
        "sha256": "ab" * 32,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


@asynccontextmanager
async def client_for(auth: FakeAuth, storage: FakeStorage) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    app.state.storage = storage
    app.state.settings = SimpleNamespace(
        storage_quarantine_bucket="appsell-quarantine",
        storage_ready_bucket="appsell-ready",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_list_assets_maps_internal_active_draft_to_public_ready_status() -> None:
    asset_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[],
        execute_values=[
            {
                "assets": [
                    {
                        "asset_id": asset_id,
                        "title": "Material seguro",
                        "file_name": "material.pdf",
                        "byte_size": 128,
                        "media_type": "application/pdf",
                        "status": "active",
                    }
                ],
                "uploads": [],
            }
        ],
    )
    storage = FakeStorage(uuid4())

    async with client_for(auth, storage) as client:
        response = await client.get(asset_path())

    assert response.status_code == 200
    assert response.json()["assets"] == [
        {
            "id": str(asset_id),
            "title": "Material seguro",
            "file_name": "material.pdf",
            "byte_size": 128,
            "media_type": "application/pdf",
            "status": "ready",
        }
    ]


@pytest.mark.asyncio
async def test_create_intent_uses_server_bucket_key_and_internal_signing_context() -> None:
    upload_id, asset_id, blob_id = uuid4(), uuid4(), uuid4()
    durable_expiry = datetime.now(UTC) + timedelta(minutes=15)
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            {
                "upload_id": upload_id,
                "asset_id": asset_id,
                "blob_id": blob_id,
                "created": True,
            },
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "application/pdf",
                "byte_size": 128,
                "sha256": "ab" * 32,
                "expires_at": durable_expiry,
            },
        ],
    )
    storage = FakeStorage(upload_id)
    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path("/upload-intents"),
            content=intent_body(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["upload_id"] == str(upload_id)
    assert "object_key" not in payload and "bucket" not in payload
    assert payload["required_headers"]["x-amz-meta-appsell-upload-id"] == str(upload_id)
    browser_expiry = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    remaining_seconds = (browser_expiry - datetime.now(UTC)).total_seconds()
    assert 250 <= remaining_seconds <= 300
    assert browser_expiry < durable_expiry
    assert auth.authenticated_capabilities == ["catalog.asset.create"]
    assert auth.context_capabilities == ["catalog.asset.create", "catalog.asset.upload.sign"]
    assert storage.presign_calls[0]["key"] == f"tenants/{SELLER}/quarantine/{upload_id}"
    create_statement, create_parameters = auth.session.calls[0]
    assert "create_asset_upload_intent" in create_statement
    assert "object_key" not in create_parameters
    assert create_parameters["quarantine_bucket"] == "appsell-quarantine"
    assert create_parameters["ready_bucket"] == "appsell-ready"


@pytest.mark.asyncio
async def test_confirm_pins_observed_version_checksum_and_metadata() -> None:
    upload_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            None,
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "application/pdf",
                "byte_size": 128,
                "sha256": "ab" * 32,
            },
            {"upload_id": upload_id, "status": "uploaded", "confirmed": True},
        ],
    )
    storage = FakeStorage(upload_id)
    body = json.dumps({"idempotency_key": str(uuid4())}).encode()
    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/confirm"),
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "uploaded"
    assert auth.context_capabilities == [
        "catalog.asset.upload.confirm",
        "catalog.asset.upload.sign",
        "catalog.asset.upload.confirm",
    ]
    assert "replay_asset_upload_confirmation" in auth.session.calls[0][0]
    confirm_statement, parameters = auth.session.calls[2]
    assert "confirm_asset_upload" in confirm_statement
    assert parameters["version_id"] == "source-v1"
    assert parameters["checksum"] == "ab" * 32


@pytest.mark.asyncio
async def test_confirm_rejects_storage_metadata_for_another_upload() -> None:
    upload_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            None,
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "application/pdf",
                "byte_size": 128,
                "sha256": "ab" * 32,
            },
        ],
    )
    storage = FakeStorage(upload_id)
    storage.observed_upload_id = str(uuid4())
    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/confirm"),
            content=json.dumps({"idempotency_key": str(uuid4())}).encode(),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422
    assert len(auth.session.calls) == 2


@pytest.mark.asyncio
async def test_confirm_replays_lost_response_without_reading_storage_again() -> None:
    upload_id = uuid4()
    replay = {
        "upload_id": upload_id,
        "asset_id": uuid4(),
        "status": "uploaded",
        "confirmed": True,
    }
    auth = FakeAuth(seller=SELLER, scalar_values=[replay])
    storage = FakeStorage(upload_id)
    body = json.dumps({"idempotency_key": str(uuid4())}).encode()

    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/confirm"),
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "uploaded"
    assert storage.head_calls == []
    assert len(auth.session.calls) == 1
    assert "replay_asset_upload_confirmation" in auth.session.calls[0][0]


@pytest.mark.asyncio
async def test_envelope_seller_mismatch_fails_before_database_or_storage() -> None:
    auth = FakeAuth(seller=OTHER_SELLER, scalar_values=[])
    storage = FakeStorage(uuid4())
    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path("/upload-intents"),
            content=intent_body(),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 401
    assert auth.session.calls == []
    assert storage.presign_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        intent_body(byte_size=10 * 1024 * 1024 + 1),
        intent_body(file_name="../secret.pdf"),
        intent_body(media_type="text/html"),
        intent_body(sha256="AB" * 32),
        intent_body(object_key="attacker/chosen"),
    ],
)
async def test_invalid_or_storage_authoritative_fields_are_rejected(body: bytes) -> None:
    auth = FakeAuth(seller=SELLER, scalar_values=[])
    storage = FakeStorage(uuid4())
    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path("/upload-intents"),
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422
    assert auth.session.calls == []
    assert storage.presign_calls == []
