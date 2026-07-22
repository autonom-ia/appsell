from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from appsell_api.asset_routes import router
from appsell_api.config import Settings
from appsell_api.internal_requests import AuthenticatedInternalRequest
from appsell_api.reset_demo_tenant import load_manifest, reset_demo
from appsell_api.service_auth import ServiceEnvelope, ServiceIdentity
from appsell_api.storage_s3 import PresignedRequest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
ISSUER = "https://identity.demo.test.autonomia.site"

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)


@pytest.fixture(autouse=True)
def pristine_demo() -> Generator[None, None, None]:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.commit()
    yield
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.commit()


class DatabaseRouteAuth:
    def __init__(
        self,
        *,
        seller: UUID,
        actor: UUID,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.seller = seller
        self.actor = actor
        self.session_factory = session_factory

    async def authenticate(
        self, request: Any, capability: str
    ) -> AuthenticatedInternalRequest:
        body = await request.body()
        now = int(time.time())
        return AuthenticatedInternalRequest(
            envelope=ServiceEnvelope(
                version=1,
                kid="postgres-route-test-v1",
                issuer="appsell-bff-test",
                audience="appsell-api-test",
                session_ref="postgres-route-test",
                session_epoch=1,
                identity=ServiceIdentity(
                    issuer=ISSUER,
                    sub="demo-admin-v1",
                    user_id=self.actor,
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
            authenticator=self,
        )

    @asynccontextmanager
    async def domain_session(self, envelope: ServiceEnvelope) -> AsyncIterator[AsyncSession]:
        async with self._context_session(envelope) as session:
            yield session

    @asynccontextmanager
    async def continuation_domain_session(
        self, envelope: ServiceEnvelope
    ) -> AsyncIterator[AsyncSession]:
        async with self._context_session(envelope) as session:
            yield session

    @asynccontextmanager
    async def _context_session(
        self, envelope: ServiceEnvelope
    ) -> AsyncIterator[AsyncSession]:
        assert CONTEXT_KEY_HEX
        issued_at = int(time.time())
        expires_at = issued_at + 60
        seller = str(envelope.seller_organization_id or "")
        payload = (
            f"{envelope.identity.user_id}:{seller}:{envelope.capability}:"
            f"{issued_at}:{expires_at}"
        )
        signature = hmac.new(
            bytes.fromhex(CONTEXT_KEY_HEX), payload.encode(), hashlib.sha256
        ).hexdigest()
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "select app_private.apply_request_context("
                    ":actor,:seller,:capability,:issued_at,:expires_at,:signature)"
                ),
                {
                    "actor": envelope.identity.user_id,
                    "seller": envelope.seller_organization_id,
                    "capability": envelope.capability,
                    "issued_at": issued_at,
                    "expires_at": expires_at,
                    "signature": signature,
                },
            )
            yield session

    async def consume_rejected_request(self, _envelope: ServiceEnvelope) -> None:
        pytest.fail("configured S3 storage unexpectedly fell back to the disabled path")


class RecordingS3:
    def __init__(self) -> None:
        self.presign_calls: list[dict[str, Any]] = []

    def presign_put(self, **kwargs: Any) -> PresignedRequest:
        self.presign_calls.append(kwargs)
        return PresignedRequest(
            url="https://appsell-quarantine-test.s3.amazonaws.com/signed",
            headers={
                "content-type": "application/pdf",
                "x-amz-checksum-sha256": "test-checksum",
                "x-amz-meta-appsell-upload-id": kwargs["upload_request_id"],
            },
            expires_in_seconds=300,
            required_content_length=128,
        )


@pytest.mark.asyncio
async def test_s3_upload_intent_route_persists_distinct_buckets_without_fallback() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    actor = uuid5(NAMESPACE_URL, f"{ISSUER}\0{manifest['personas']['admin']['subject']}")
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    runtime_async_url = RUNTIME_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(runtime_async_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    auth = DatabaseRouteAuth(seller=seller, actor=actor, session_factory=session_factory)
    storage = RecordingS3()
    settings = Settings(
        environment="ci",
        storage_provider="s3",
        storage_quarantine_bucket="appsell-quarantine-test",
        storage_ready_bucket="appsell-ready-test",
        storage_region="sa-east-1",
        storage_allowed_origin="http://localhost:3000",
        malware_scanner_provider="deterministic",
        pdf_sandbox_socket="/run/appsell-pdf/pdf.sock",
    )
    assert settings.storage_provider == "s3"
    assert settings.private_learning_asset_root == ""

    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    app.state.storage = storage
    app.state.settings = settings
    path = (
        f"/v1/me/sellers/{seller}/products/{product}/courses/{course}"
        f"/modules/{module}/lessons/{lesson}/assets/upload-intents"
    )
    request_body = {
        "idempotency_key": str(uuid4()),
        "expected_course_version": 1,
        "title": "Upload S3 integrado",
        "file_name": "integrado.pdf",
        "media_type": "application/pdf",
        "byte_size": 128,
        "sha256": hashlib.sha256(b"integrated-s3-upload").hexdigest(),
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as client:
            response = await client.post(
                path,
                content=json.dumps(request_body, separators=(",", ":")),
                headers={"content-type": "application/json"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 201, response.text
    payload = response.json()
    assert "bucket" not in payload and "object_key" not in payload
    assert storage.presign_calls == [
        {
            "bucket": "appsell-quarantine-test",
            "key": f"tenants/{seller}/quarantine/{payload['upload_id']}",
            "content_type": "application/pdf",
            "sha256_hex": request_body["sha256"],
            "byte_size": 128,
            "upload_request_id": payload["upload_id"],
        }
    ]
    with psycopg.connect(ADMIN_URL) as connection:
        persisted = connection.execute(
            "select storage_driver,quarantine_bucket,ready_bucket,status "
            "from app_private.asset_upload where seller_organization_id=%s and id=%s",
            (seller, UUID(payload["upload_id"])),
        ).fetchone()
    assert persisted == (
        "s3",
        "appsell-quarantine-test",
        "appsell-ready-test",
        "pending",
    )
