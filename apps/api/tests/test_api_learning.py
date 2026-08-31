from __future__ import annotations

import asyncio
import base64
import hashlib
import io
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
from appsell_api.pdf_links import PdfLinksBusy
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


def pdf_study_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "expected_revision": 0,
        "mutation": {
            "action": "annotation.upsert",
            "annotation": {
                "id": str(uuid4()),
                "page": 2,
                "tool": "pen",
                "color": "#173B73",
                "width": 0.003,
                "points": [{"x": 0.1, "y": 0.2}, {"x": 0.4, "y": 0.5}],
            },
        },
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


def pdf_study_state() -> dict[str, Any]:
    return {
        "asset_id": str(ASSET_ID),
        "asset_version": 1,
        "page_count": 2,
        "revision": 1,
        "annotations": [],
        "bookmarks": [2],
        "updated_at": "2026-08-24T12:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_pdf_study_read_is_buyer_scoped_and_version_pinned() -> None:
    auth = FakeAuth(pdf_study_state())
    async with client_for(auth) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/study"
        )
    assert response.status_code == 200
    assert response.json() == {"state": pdf_study_state()}
    assert auth.capabilities == ["learning.pdf-study.read"]
    assert "read_pdf_study_state" in auth.session.calls[0][0]
    assert auth.session.calls[0][1]["identity_user_id"] == IDENTITY_ID


@pytest.mark.asyncio
async def test_pdf_study_mutation_forwards_only_valid_normalized_operation() -> None:
    auth = FakeAuth(pdf_study_state())
    body = pdf_study_body()
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/study",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200
    assert response.json() == {"state": pdf_study_state()}
    assert auth.capabilities == ["learning.pdf-study.write"]
    statement, parameters = auth.session.calls[0]
    assert "mutate_pdf_study_state" in statement
    mutation = json.loads(parameters["mutation"])
    assert mutation["action"] == "annotation.upsert"
    assert mutation["annotation"]["points"][-1] == {"x": 0.4, "y": 0.5}
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_pdf_study_rejects_unknown_fields_and_oversized_vectors() -> None:
    auth = FakeAuth(pdf_study_state())
    invalid = json.loads(pdf_study_body())
    invalid["mutation"]["annotation"]["unexpected"] = True
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/study",
            json=invalid,
        )
    assert response.status_code == 422
    assert auth.domain_sessions == 0
    assert auth.rejected_requests == 1

    oversized_auth = FakeAuth(pdf_study_state())
    oversized = pdf_study_body(
        mutation={
            "action": "annotation.upsert",
            "annotation": {
                "id": str(uuid4()),
                "page": 1,
                "tool": "pen",
                "color": "#173B73",
                "width": 0.003,
                "points": [{"x": 0.1, "y": 0.2}] * 5000,
            },
        }
    )
    async with client_for(oversized_auth) as client:
        response = await client.post(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/study",
            content=oversized,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert oversized_auth.domain_sessions == 0


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
    auth = FakeAuth(
        pdf_manifest_row(page_count=7),
        execute_results=[pdf_manifest_row(page_count=7), asset_row()],
    )
    async with client_for(auth, settings=isolated_viewer_settings()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages"
        )

    assert response.status_code == 200
    assert response.json() == {"asset": {"id": str(ASSET_ID), "page_count": 7, "links": []}}
    assert response.headers["content-type"] == "application/json"
    assert auth.capabilities == ["library.asset.read"]
    assert "authorize_entitled_pdf_manifest" in auth.session.calls[0][0]
    assert "object_key" not in response.text and "storage_bucket" not in response.text


@pytest.mark.asyncio
async def test_pdf_manifest_extracts_links_from_the_pinned_ready_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = pdf_manifest_row(page_count=2)
    source = {
        **asset_row(),
        "storage_driver": "s3",
        "storage_bucket": "appsell-ready",
        "object_key": "tenants/seller/assets/asset/v2/blob",
        "storage_version_id": "ready-v2",
        "storage_etag": "etag-v2",
    }
    auth = FakeAuth(manifest, execute_results=[manifest, source])
    opened: list[dict[str, Any]] = []

    class Storage:
        @contextmanager
        def open_download(self, **kwargs: Any):  # type: ignore[no-untyped-def]
            opened.append(kwargs)
            yield io.BytesIO(b"pdf")

    def extracted(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "page_count": 2,
            "links": [
                {
                    "id": "0123456789abcdef",
                    "page": 1,
                    "kind": "page",
                    "target_page": 2,
                    "rect": {"left": 10.0, "top": 10.0, "width": 80.0, "height": 20.0},
                }
            ],
        }

    learning_routes._PDF_LINK_CACHE.clear()
    monkeypatch.setattr(learning_routes, "read_remote_pdf_link_manifest", extracted)
    async with client_for(auth, Storage(), isolated_viewer_settings()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages"
        )

    assert response.status_code == 200
    assert opened == [
        {
            "bucket": "appsell-ready",
            "key": "tenants/seller/assets/asset/v2/blob",
            "version_id": "ready-v2",
        }
    ]
    link = response.json()["asset"]["links"][0]
    assert link["target_page"] == 2
    assert link["rect"] == {
        "left": 10.0,
        "top": 9.7087,
        "width": 80.0,
        "height": 19.4175,
    }


@pytest.mark.asyncio
async def test_pdf_manifest_insiste_quando_o_servico_isolado_esta_ocupado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rota entrega os links depois de o serviço isolado se desocupar.

    Ele tem um lugar só, e nele cabem a leitura dos links, a rasterização das
    páginas e a conversão dos vídeos. Quem chega junto leva `429` — e isso caía
    no `return []`: o aluno abria o guia sem botão nenhum, sem erro na tela e
    sem registro. Bastava outro aluno abrindo um PDF ao mesmo tempo.

    Este teste passa pela rota inteira porque o sintoma era da rota: o que
    importa não é o auxiliar saber insistir, é o documento chegar com links.
    """
    manifest = pdf_manifest_row(page_count=2)
    source = {
        **asset_row(),
        "storage_driver": "s3",
        "storage_bucket": "appsell-ready",
        "object_key": "tenants/seller/assets/asset/v2/blob",
        "storage_version_id": "ready-v2",
        "storage_etag": "etag-v2",
    }
    auth = FakeAuth(manifest, execute_results=[manifest, source])

    class Storage:
        @contextmanager
        def open_download(self, **kwargs: Any):  # type: ignore[no-untyped-def]
            yield io.BytesIO(b"pdf")

    tentativas = 0

    def ocupado_e_depois_livre(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal tentativas
        tentativas += 1
        if tentativas < 3:
            raise PdfLinksBusy("isolated PDF link service is busy")
        return {
            "schema_version": 1,
            "page_count": 2,
            "links": [
                {
                    "id": "0123456789abcdef",
                    "page": 1,
                    "kind": "page",
                    "target_page": 2,
                    "rect": {"left": 10.0, "top": 10.0, "width": 80.0, "height": 20.0},
                }
            ],
        }

    learning_routes._PDF_LINK_CACHE.clear()
    monkeypatch.setattr(learning_routes, "PDF_LINKS_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(learning_routes, "read_remote_pdf_link_manifest", ocupado_e_depois_livre)
    async with client_for(auth, Storage(), isolated_viewer_settings()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages"
        )

    assert response.status_code == 200
    assert tentativas == 3
    # O que importa: o documento chegou com o botão, e não vazio.
    assert response.json()["asset"]["links"][0]["id"] == "0123456789abcdef"


@pytest.mark.asyncio
async def test_pdf_manifest_volta_sem_links_se_o_servico_nao_desocupar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Insistir tem fim, e o fim é o comportamento antigo.

    Uma conversão de vídeo longa não desocupa em quatro tentativas. Aí o
    documento volta sem links — como voltava antes —, mas só depois de tentar, e
    a rota não fica pendurada além do prazo que o BFF dá a ela.
    """
    manifest = pdf_manifest_row(page_count=2)
    source = {
        **asset_row(),
        "storage_driver": "s3",
        "storage_bucket": "appsell-ready",
        "object_key": "tenants/seller/assets/asset/v2/blob",
        "storage_version_id": "ready-v2",
        "storage_etag": "etag-v2",
    }
    auth = FakeAuth(manifest, execute_results=[manifest, source])

    class Storage:
        @contextmanager
        def open_download(self, **kwargs: Any):  # type: ignore[no-untyped-def]
            yield io.BytesIO(b"pdf")

    tentativas = 0

    def sempre_ocupado(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal tentativas
        tentativas += 1
        raise PdfLinksBusy("isolated PDF link service is busy")

    learning_routes._PDF_LINK_CACHE.clear()
    monkeypatch.setattr(learning_routes, "PDF_LINKS_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(learning_routes, "read_remote_pdf_link_manifest", sempre_ocupado)
    async with client_for(auth, Storage(), isolated_viewer_settings()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages"
        )

    assert response.status_code == 200
    assert tentativas == learning_routes.PDF_LINKS_ATTEMPTS
    assert response.json()["asset"]["links"] == []


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
async def test_pdf_viewer_rejects_excess_parallel_work_before_spooling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = FakeAuth(
        pdf_manifest_row(),
        execute_results=[pdf_manifest_row(), pdf_page_row()],
    )
    slots = asyncio.Semaphore(1)
    acquired = await slots.acquire()
    assert acquired is True
    monkeypatch.setattr(learning_routes, "_PROTECTED_PDF_SLOTS", slots)
    monkeypatch.setattr(learning_routes, "_PROTECTED_PDF_SLOT_TIMEOUT_SECONDS", 0.01)
    try:
        async with client_for(auth, settings=isolated_viewer_settings()) as client:
            response = await client.get(
                f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages/1"
            )
    finally:
        slots.release()

    assert response.status_code == 429
    assert response.json() == {"detail": "pdf_viewer_busy"}
    assert response.headers["retry-after"] == "2"


@pytest.mark.asyncio
async def test_pdf_viewer_waits_for_a_short_capacity_spike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = FakeAuth(
        pdf_manifest_row(page_count=3),
        execute_results=[pdf_manifest_row(page_count=3), pdf_page_row(page_number=2)],
    )
    slots = asyncio.Semaphore(1)
    await slots.acquire()
    monkeypatch.setattr(learning_routes, "_PROTECTED_PDF_SLOTS", slots)
    monkeypatch.setattr(learning_routes, "_PROTECTED_PDF_SLOT_TIMEOUT_SECONDS", 0.2, raising=False)
    png = b"\x89PNG\r\n\x1a\nprotected-page"

    @contextmanager
    def source(*_args: Any, **_kwargs: Any):  # type: ignore[no-untyped-def]
        yield Path("/verified/page-2.png")

    def render(*_args: Any, **_kwargs: Any) -> RenderedLearningPdfPage:
        return RenderedLearningPdfPage(
            payload=png,
            page_count=3,
            page_number=2,
            sha256_hex=hashlib.sha256(png).hexdigest(),
        )

    monkeypatch.setattr(learning_routes, "authorized_pdf_page", source)
    monkeypatch.setattr(learning_routes, "render_prepared_pdf_page", render)

    async def release_slot() -> None:
        await asyncio.sleep(0.02)
        slots.release()

    releaser = asyncio.create_task(release_slot())
    async with client_for(auth, settings=isolated_viewer_settings()) as client:
        response = await client.get(
            f"/v1/me/learning/items/{ENTITLEMENT_ID}/assets/{ASSET_ID}/pages/2"
        )
    await releaser

    assert response.status_code == 200
    assert response.content == png


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
        "pages": [
            {
                "page_number": page_number,
                "byte_size": 120,
                "sha256": "cd" * 32,
                "pixel_width": 800,
                "pixel_height": 1200,
            }
            for page_number in range(1, page_count + 1)
        ],
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


@pytest.mark.asyncio
async def test_access_link_redemption_sends_only_the_digest_and_reports_the_seat() -> None:
    # The BFF hashes the token before it reaches this service. If a raw token
    # ever arrived here it would end up in request logs and in the database.
    product_id = uuid4()
    auth = FakeAuth(
        {
            "entitlement_id": ENTITLEMENT_ID,
            "seller_organization_id": SELLER_ID,
            "catalog_product_id": product_id,
            "created": True,
            "seat_consumed": True,
            "seats_remaining": 17,
        }
    )
    body = {"idempotency_key": str(uuid4()), "token_sha256": "a" * 64}
    async with client_for(auth) as client:
        response = await client.post("/v1/me/library/access-links/redeem", json=body)

    assert response.status_code == 201
    assert response.json() == {
        "status": "active",
        "entitlement_id": str(ENTITLEMENT_ID),
        "seller_id": str(SELLER_ID),
        "product_id": str(product_id),
        "created": True,
        "seat_consumed": True,
        "seats_remaining": 17,
    }
    assert auth.capabilities == ["library.enroll"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.redeem_product_access_link" in statement
    assert parameters["token_sha256"] == "a" * 64
    assert parameters["identity_user_id"] == IDENTITY_ID


@pytest.mark.asyncio
async def test_access_link_re_entry_reports_no_seat_consumed() -> None:
    # Reopening the link must not burn a second seat, otherwise a thirty seat
    # class is exhausted by thirty people refreshing the page.
    product_id = uuid4()
    auth = FakeAuth(
        {
            "entitlement_id": ENTITLEMENT_ID,
            "seller_organization_id": SELLER_ID,
            "catalog_product_id": product_id,
            "created": False,
            "seat_consumed": False,
            "seats_remaining": 17,
        }
    )
    body = {"idempotency_key": str(uuid4()), "token_sha256": "b" * 64}
    async with client_for(auth) as client:
        response = await client.post("/v1/me/library/access-links/redeem", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["seat_consumed"] is False
    assert payload["seats_remaining"] == 17


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"idempotency_key": "not-a-uuid", "token_sha256": "a" * 64},
        {"idempotency_key": str(uuid4()), "token_sha256": "a" * 63},
        {"idempotency_key": str(uuid4()), "token_sha256": "z" * 64},
        {"idempotency_key": str(uuid4()), "token_sha256": "A" * 64},
        {"idempotency_key": str(uuid4())},
        {"idempotency_key": str(uuid4()), "token_sha256": "a" * 64, "extra": 1},
    ],
)
async def test_access_link_redemption_rejects_malformed_bodies(body: dict[str, Any]) -> None:
    auth = FakeAuth({})
    async with client_for(auth) as client:
        response = await client.post("/v1/me/library/access-links/redeem", json=body)

    assert response.status_code == 422
    assert auth.domain_sessions == 0


def test_access_link_learning_errors_stay_indistinguishable_for_unknown_tokens() -> None:
    from appsell_api.learning_routes import _learning_domain_error

    for sqlstate, expected_status, expected_detail in [
        ("P4224", 409, "access_link_exhausted"),
        ("P4225", 404, "access_link_unavailable"),
        ("P4222", 409, "offer_requires_checkout"),
    ]:
        mapped = _learning_domain_error(
            SimpleNamespace(orig=SimpleNamespace(sqlstate=sqlstate))  # type: ignore[arg-type]
        )
        assert (mapped.status_code, mapped.detail) == (expected_status, expected_detail)


def test_o_sqlstate_nao_mapeado_deixa_rastro_antes_do_503() -> None:
    """O motivo do 503 tem de sobrar em algum lugar.

    Em 30/08/2026 um aluno não conseguiu abrir a aula e recebeu "verifique sua
    conexão". A requisição tinha chegado ao servidor e voltado em 160 ms.
    Durante duas tentativas, `web` e `api` não escreveram uma única linha: o
    erro era descartado pelo catch-all dos dois lados.

    Uma investigação de uma hora descartou publicação, funções do banco, rota,
    chave interna, Redis e saúde da API — e não chegou ao motivo, porque o
    motivo tinha sido jogado fora exatamente aqui.

    O sqlstate é um código de cinco caracteres: nomeia a classe do erro, nunca o
    dado que o causou.
    """
    import structlog

    from appsell_api.logging_setup import configure_logging_from_environment

    captura = structlog.testing.LogCapture()
    structlog.configure(processors=[captura])
    try:
        error = SimpleNamespace(orig=SimpleNamespace(sqlstate="XX000"))
        mapped = _learning_domain_error(error)  # type: ignore[arg-type]
    finally:
        # `reset_defaults` restaura o padrao do structlog, nao o desta
        # aplicacao: ele desligaria o renderizador JSON e o redator.
        configure_logging_from_environment()

    assert mapped.status_code == 503
    assert mapped.detail == "learning_unavailable"
    eventos = [e for e in captura.entries if e.get("event") == "learning_domain_error_unmapped"]
    assert eventos, "o 503 saiu sem deixar rastro do sqlstate"
    assert eventos[0]["sqlstate"] == "XX000"


def test_um_sqlstate_ja_mapeado_nao_vira_ruido_no_log() -> None:
    """Só o desconhecido merece aviso; o mapeado já tem resposta própria."""
    import structlog

    from appsell_api.logging_setup import configure_logging_from_environment

    captura = structlog.testing.LogCapture()
    structlog.configure(processors=[captura])
    try:
        error = SimpleNamespace(orig=SimpleNamespace(sqlstate="28000"))
        mapped = _learning_domain_error(error)  # type: ignore[arg-type]
    finally:
        # `reset_defaults` restaura o padrao do structlog, nao o desta
        # aplicacao: ele desligaria o renderizador JSON e o redator.
        configure_logging_from_environment()

    assert mapped.status_code == 404
    assert not [e for e in captura.entries if e.get("event") == "learning_domain_error_unmapped"]


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("XX000", "XX000"),
        ("28000", "28000"),
        (None, "ausente_ou_invalido"),
        ("", "ausente_ou_invalido"),
        ("joao@exemplo.com", "ausente_ou_invalido"),
        ("select * from identity_user", "ausente_ou_invalido"),
        ("xx000", "ausente_ou_invalido"),
    ],
)
def test_so_a_forma_de_um_sqlstate_entra_no_log(bruto: object, esperado: str) -> None:
    """Prometer "cinco caracteres, nenhum valor" sem impor a forma não é promessa.

    O sqlstate vem do driver e hoje é sempre um código de cinco caracteres — mas
    "hoje é sempre" foi exatamente a suposição que deixou passar o vazamento
    equivalente no BFF, onde o código do erro vinha do `detail` da resposta sem
    validação nenhuma.
    """
    from appsell_api.learning_routes import _safe_sqlstate

    assert _safe_sqlstate(bruto) == esperado


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("OperationalError", "OperationalError"),
        ("ProgrammingError", "ProgrammingError"),
        ("joao@exemplo.com", "desconhecido"),
        ("classe com espaco", "desconhecido"),
        ("", "desconhecido"),
        ("A" * 80, "desconhecido"),
    ],
)
def test_so_um_identificador_entra_no_log(bruto: str, esperado: str) -> None:
    """O nome da classe do erro, e nada além disso."""
    from appsell_api.learning_routes import _safe_identifier

    assert _safe_identifier(bruto) == esperado
