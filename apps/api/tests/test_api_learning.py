from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from appsell_api import learning_routes
from appsell_api.config import Settings
from appsell_api.learning_pdf import RenderedLearningPdfPage
from appsell_api.learning_routes import _learning_domain_error, router
from appsell_api.storage_s3 import PresignedRequest

IDENTITY_ID = UUID("81000000-0000-4000-8000-000000000001")
SELLER_ID = UUID("82000000-0000-4000-8000-000000000001")
ENTITLEMENT_ID = UUID("83000000-0000-4000-8000-000000000001")
LESSON_ID = UUID("84000000-0000-4000-8000-000000000001")
ASSET_ID = UUID("85000000-0000-4000-8000-000000000001")


class FakeSession:
    def __init__(self, result: Any, execute_results: list[Any] | None = None) -> None:
        self.result = result
        self.execute_results = list(execute_results or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, statement: Any, parameters: dict[str, Any]) -> Any:
        self.calls.append((str(statement), parameters))
        return self.result

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> Any:
        self.calls.append((str(statement), parameters))
        return FakeMappingResult(
            self.execute_results.pop(0) if self.execute_results else self.result
        )


class FakeMappingResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def mappings(self) -> FakeMappingResult:
        return self

    def one(self) -> Any:
        return self.value


class FakeAuth:
    def __init__(
        self,
        result: Any,
        *,
        seller_id: UUID | None = None,
        execute_results: list[Any] | None = None,
    ) -> None:
        self.session = FakeSession(result, execute_results)
        self.seller_id = seller_id
        self.capabilities: list[str] = []
        self.domain_sessions = 0
        self.rejected_requests = 0
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
            consume_rejected_request=self.consume_rejected_request,
        )

    async def consume_rejected_request(self) -> None:
        self.rejected_requests += 1

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[FakeSession]:
        self.domain_sessions += 1
        yield self.session


@asynccontextmanager
async def client_for(
    auth: FakeAuth, storage: Any = None, settings: Settings | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    app.state.storage = storage
    app.state.settings = settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        yield client


def isolated_viewer_settings() -> Settings:
    return Settings(
        internal_issuer="",
        internal_audience="",
        identity_issuer="",
        internal_hmac_keyring_b64_json="",
        context_hmac_key_b64="",
        service_credential_b64="",
    )


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
async def test_pdf_asset_authorization_never_returns_original_locator() -> None:
    auth = FakeAuth(
        {
            "asset_id": ASSET_ID,
            "asset_version": 1,
            "file_name": "codex-lesson-1.pdf",
            "title": "Aula Codex",
            "media_type": "application/pdf",
            "byte_size": 128,
            "sha256": "ab" * 32,
            "storage_driver": "legacy_bundle",
            "storage_bucket": "legacy",
            "object_key": "legacy",
            "storage_version_id": "legacy",
            "storage_etag": "legacy",
        }
    )
    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}")
    assert response.status_code == 200
    assert response.json()["asset"] == {
        "id": str(ASSET_ID),
        "delivery": "protected_pages",
        "file_name": "codex-lesson-1.pdf",
        "title": "Aula Codex",
        "media_type": "application/pdf",
        "byte_size": 128,
        "sha256": "ab" * 32,
    }
    assert "authorize_publication_asset" in auth.session.calls[0][0]


@pytest.mark.asyncio
async def test_public_free_access_enrollment_is_buyer_scoped_and_idempotent() -> None:
    auth = FakeAuth({"entitlement_id": ENTITLEMENT_ID, "created": True})
    body = {
        "idempotency_key": str(uuid4()),
        "seller_id": str(SELLER_ID),
        "product_id": str(uuid4()),
    }
    async with client_for(auth) as client:
        response = await client.post("/v1/me/library/enroll", json=body)

    assert response.status_code == 201
    assert response.json() == {
        "status": "active",
        "entitlement_id": str(ENTITLEMENT_ID),
        "created": True,
    }
    assert auth.capabilities == ["library.enroll"]
    assert auth.session.calls[0][1]["seller_id"] == SELLER_ID
    assert auth.session.calls[0][1]["product_id"] == UUID(body["product_id"])
    assert auth.session.calls[0][1]["identity_user_id"] == IDENTITY_ID


@pytest.mark.asyncio
async def test_s3_pdf_asset_does_not_create_a_signed_original_url() -> None:
    auth = FakeAuth(
        {
            "asset_id": ASSET_ID,
            "asset_version": 2,
            "file_name": "material seguro.pdf",
            "title": "Material seguro",
            "media_type": "application/pdf",
            "byte_size": 120,
            "sha256": "cd" * 32,
            "storage_driver": "s3",
            "storage_bucket": "appsell-ready",
            "object_key": "tenants/seller/assets/asset/v2/blob",
            "storage_version_id": "ready-v2",
            "storage_etag": "etag-v2",
        }
    )
    calls: list[dict[str, Any]] = []

    class Storage:
        def presign_get(self, **kwargs: Any) -> PresignedRequest:
            calls.append(kwargs)
            return PresignedRequest(
                url="http://127.0.0.1:8333/ready/key?X-Amz-Signature=signed",
                headers={},
                expires_in_seconds=60,
            )

    async with client_for(auth, Storage()) as client:
        response = await client.get(f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}")
    assert response.status_code == 200
    asset = response.json()["asset"]
    assert asset["delivery"] == "protected_pages"
    assert "download_url" not in asset
    assert "storage_bucket" not in asset and "object_key" not in asset
    assert calls == []


@pytest.mark.asyncio
async def test_download_requires_separate_capability_and_presigns_attachment() -> None:
    auth = FakeAuth(
        {
            "asset_id": ASSET_ID,
            "asset_version": 2,
            "file_name": "apoio.pdf",
            "title": "Material de apoio",
            "media_type": "application/pdf",
            "byte_size": 120,
            "sha256": "cd" * 32,
            "storage_driver": "s3",
            "storage_bucket": "appsell-ready",
            "object_key": "tenants/seller/assets/asset/v2/blob",
            "storage_version_id": "ready-v2",
            "storage_etag": "etag-v2",
        }
    )
    calls: list[dict[str, Any]] = []

    class Storage:
        def presign_get(self, **kwargs: Any) -> PresignedRequest:
            calls.append(kwargs)
            return PresignedRequest(
                url="http://127.0.0.1:8333/ready/key?X-Amz-Signature=signed",
                headers={},
                expires_in_seconds=60,
            )

    async with client_for(auth, Storage()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/download"
        )
    assert response.status_code == 200
    assert auth.capabilities == ["library.asset.download"]
    assert "authorize_publication_asset_download" in auth.session.calls[0][0]
    assert calls[0]["disposition"] == "attachment"


@pytest.mark.asyncio
async def test_pdf_manifest_is_entitlement_scoped_and_never_returns_original() -> None:
    auth = FakeAuth(pdf_manifest_row(page_count=7))
    async with client_for(auth, settings=isolated_viewer_settings()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages"
        )

    assert response.status_code == 200
    assert response.json() == {"asset": {"id": str(ASSET_ID), "page_count": 7}}
    assert response.headers["content-type"] == "application/json"
    assert auth.capabilities == ["library.asset.read"]
    assert "authorize_entitled_pdf_manifest" in auth.session.calls[0][0]
    assert "object_key" not in response.text and "storage_bucket" not in response.text


@pytest.mark.asyncio
async def test_pdf_page_returns_only_licensed_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = FakeAuth(
        pdf_manifest_row(page_count=3),
        execute_results=[pdf_manifest_row(page_count=3), pdf_page_row(page_number=2)],
    )
    png = b"\x89PNG\r\n\x1a\nprotected-page"
    observed: dict[str, Any] = {}

    @contextmanager
    def source(*_args: Any, **_kwargs: Any):  # type: ignore[no-untyped-def]
        yield Path("/verified/page-2.png")

    def render(*_args: Any, **kwargs: Any) -> RenderedLearningPdfPage:
        observed.update(kwargs)
        return RenderedLearningPdfPage(
            payload=png,
            page_count=3,
            page_number=2,
            sha256_hex=hashlib.sha256(png).hexdigest(),
        )

    monkeypatch.setattr(learning_routes, "authorized_pdf_page", source)
    monkeypatch.setattr(learning_routes, "render_prepared_pdf_page", render)
    license_label = "Licenciado para bu***@example.test • ID 2a5704"
    encoded_label = base64.urlsafe_b64encode(license_label.encode()).decode().rstrip("=")
    async with client_for(auth, settings=isolated_viewer_settings()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages/2"
            f"?license={encoded_label}",
        )

    assert response.status_code == 200
    assert response.content == png
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'inline; filename="page-2.png"'
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-appsell-page-count"] == "3"
    assert observed["page_count"] == 3
    assert observed["watermark"] == license_label


@pytest.mark.asyncio
async def test_pdf_viewer_rejects_excess_parallel_work_before_spooling() -> None:
    auth = FakeAuth(
        pdf_manifest_row(),
        execute_results=[pdf_manifest_row(), pdf_page_row()],
    )
    first = await learning_routes._PROTECTED_PDF_SLOTS.acquire()
    second = await learning_routes._PROTECTED_PDF_SLOTS.acquire()
    assert first is True and second is True
    try:
        async with client_for(auth, settings=isolated_viewer_settings()) as client:
            response = await client.get(
                f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages/1"
            )
    finally:
        learning_routes._PROTECTED_PDF_SLOTS.release()
        learning_routes._PROTECTED_PDF_SLOTS.release()

    assert response.status_code == 429
    assert response.json() == {"detail": "pdf_viewer_busy"}
    assert response.headers["retry-after"] == "2"


def asset_row(media_type: str = "application/pdf") -> dict[str, Any]:
    return {
        "asset_id": ASSET_ID,
        "asset_version": 2,
        "file_name": "material.pdf" if media_type == "application/pdf" else "aula.mp4",
        "title": "Material seguro",
        "media_type": media_type,
        "byte_size": 120,
        "sha256": "cd" * 32,
        "storage_driver": "legacy_bundle",
        "storage_bucket": "codex-demo",
        "object_key": "material",
        "storage_version_id": None,
        "storage_etag": None,
    }


def pdf_manifest_row(*, page_count: int = 1) -> dict[str, Any]:
    return {
        "seller_organization_id": SELLER_ID,
        "asset_id": ASSET_ID,
        "asset_version": 2,
        "blob_id": UUID("86000000-0000-4000-8000-000000000001"),
        "raster_profile": "png-1600-v1",
        "page_count": page_count,
        "total_byte_size": page_count * 120,
        "manifest_sha256": "ab" * 32,
        "pages": [],
    }


def pdf_page_row(*, page_number: int = 1) -> dict[str, Any]:
    return {
        "seller_organization_id": SELLER_ID,
        "asset_id": ASSET_ID,
        "asset_version": 2,
        "blob_id": UUID("86000000-0000-4000-8000-000000000001"),
        "raster_profile": "png-1600-v1",
        "page_number": page_number,
        "storage_driver": "s3",
        "storage_bucket": "appsell-ready",
        "object_key": f"tenants/{SELLER_ID}/assets/{ASSET_ID}/v2/pages/{page_number}.png",
        "storage_version_id": f"page-v{page_number}",
        "storage_etag": f"page-etag-{page_number}",
        "byte_size": 120,
        "sha256": "cd" * 32,
        "pixel_width": 800,
        "pixel_height": 1200,
    }


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
    assert "app_private.read_learning_player_v2" not in statement
    assert "app_private.read_learning_player(" in statement
    assert parameters == {
        "identity_user_id": IDENTITY_ID,
        "entitlement_id": ENTITLEMENT_ID,
        "lesson_id": None,
    }


@pytest.mark.asyncio
async def test_resume_negotiates_additive_multi_asset_contract_explicitly() -> None:
    player = {
        "entitlement_id": str(ENTITLEMENT_ID),
        "selected_lesson_id": str(LESSON_ID),
        "selected_assets": [],
    }
    auth = FakeAuth(player)
    async with client_for(auth) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/resume",
            headers={"x-appsell-learning-assets-contract": "v2"},
        )

    assert response.status_code == 200
    assert response.json() == {"player": player}
    statement, _ = auth.session.calls[0]
    assert "app_private.read_learning_player_v2" in statement


@pytest.mark.asyncio
async def test_resume_negotiates_pdf_policy_contract_explicitly() -> None:
    player = {"schema_version": 3, "selected_assets": []}
    auth = FakeAuth(player)
    async with client_for(auth) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/resume",
            headers={"x-appsell-learning-assets-contract": "v3"},
        )

    assert response.status_code == 200
    statement, _ = auth.session.calls[0]
    assert "app_private.read_learning_player_v3" in statement


@pytest.mark.asyncio
async def test_unknown_learning_asset_contract_remains_on_legacy_projection() -> None:
    auth = FakeAuth({"selected_lesson_id": str(LESSON_ID)})
    async with client_for(auth) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/resume",
            headers={"x-appsell-learning-assets-contract": "v99"},
        )

    assert response.status_code == 200
    statement, _ = auth.session.calls[0]
    assert "app_private.read_learning_player_v2" not in statement


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
