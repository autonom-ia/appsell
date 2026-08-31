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

from appsell_api.asset_media import PDF_MEDIA_TYPE, max_bytes
from appsell_api.asset_routes import router
from appsell_api.internal_requests import AuthenticatedInternalRequest
from appsell_api.service_auth import ServiceEnvelope, ServiceIdentity
from appsell_api.storage_s3 import MULTIPART_PART_BYTES, PresignedRequest, UploadedPart

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
        self.rejected_requests = 0

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
            authenticator=self,
        )

    @asynccontextmanager
    async def domain_session(self, envelope: ServiceEnvelope) -> AsyncIterator[FakeSession]:
        self.context_capabilities.append(envelope.capability)
        yield self.session

    @asynccontextmanager
    async def continuation_domain_session(
        self, envelope: ServiceEnvelope
    ) -> AsyncIterator[FakeSession]:
        self.context_capabilities.append(envelope.capability)
        yield self.session

    async def consume_rejected_request(self, _envelope: ServiceEnvelope) -> None:
        self.rejected_requests += 1


class FakeStorage:
    def __init__(self, upload_id: UUID):
        self.upload_id = upload_id
        self.presign_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.observed_upload_id = str(upload_id)
        self.observed_size = 128
        self.observed_checksum_b64 = base64.b64encode(bytes.fromhex("ab" * 32)).decode()
        self.multipart_calls: list[tuple[str, dict[str, Any]]] = []
        self.open_multipart_id: str | None = None
        self.uploaded_parts: tuple[UploadedPart, ...] = ()

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
            size=self.observed_size,
            checksum_sha256_b64=self.observed_checksum_b64,
            etag="etag-v1",
        )

    # --- envio em partes ---

    put_ttl = 300

    def find_multipart_upload(self, **kwargs: Any) -> str | None:
        self.multipart_calls.append(("find", kwargs))
        return self.open_multipart_id

    def create_multipart_upload(self, **kwargs: Any) -> str:
        self.multipart_calls.append(("create", kwargs))
        self.open_multipart_id = "envio-do-storage"
        return self.open_multipart_id

    def presign_upload_part(self, **kwargs: Any) -> PresignedRequest:
        self.multipart_calls.append(("sign", kwargs))
        return PresignedRequest(
            url="http://127.0.0.1:8333/quarantine/key?partNumber=1&X-Amz-Signature=signed",
            headers={"x-amz-checksum-sha256": "checksum-da-parte"},
            expires_in_seconds=300,
            required_content_length=kwargs["plan"].bytes_for_part(kwargs["part_number"]),
        )

    def list_multipart_parts(self, **kwargs: Any) -> tuple[UploadedPart, ...]:
        self.multipart_calls.append(("list", kwargs))
        return self.uploaded_parts

    def complete_multipart_upload(self, **kwargs: Any) -> str:
        self.multipart_calls.append(("complete", kwargs))
        self.open_multipart_id = None
        return "source-v1"

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        self.multipart_calls.append(("abort", kwargs))
        self.open_multipart_id = None


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
async def client_for(
    auth: FakeAuth, storage: FakeStorage | None, *, video_upload_enabled: bool = False
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    app.state.storage = storage
    app.state.settings = SimpleNamespace(
        storage_quarantine_bucket="appsell-quarantine",
        storage_ready_bucket="appsell-ready",
        video_upload_enabled=video_upload_enabled,
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
                        "content_role": "course_content",
                        "download_allowed": False,
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
            "content_role": "course_content",
            "download_allowed": False,
        }
    ]
    assert "list_lesson_asset_drafts_v2" in auth.session.calls[0][0]


@pytest.mark.asyncio
async def test_owner_updates_pdf_policy_with_version_and_idempotency_binding() -> None:
    asset_id = uuid4()
    idempotency_key = uuid4()
    result = {
        "asset_id": str(asset_id),
        "content_role": "support_material",
        "download_allowed": True,
        "course_version": 2,
        "applied": True,
    }
    auth = FakeAuth(seller=SELLER, scalar_values=[result])
    body = json.dumps(
        {
            "idempotency_key": str(idempotency_key),
            "expected_course_version": 1,
            "content_role": "support_material",
            "download_allowed": True,
        },
        separators=(",", ":"),
    ).encode()
    async with client_for(auth, FakeStorage(uuid4())) as client:
        response = await client.patch(
            asset_path(f"/{asset_id}/policy"),
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json() == result
    assert auth.authenticated_capabilities == ["catalog.asset.policy.update"]
    statement, parameters = auth.session.calls[0]
    assert "set_lesson_asset_policy" in statement
    assert parameters["asset"] == asset_id
    assert parameters["content_role"] == "support_material"
    assert parameters["download_allowed"] is True
    assert parameters["expected_course_version"] == 1
    assert parameters["idempotency_key"] == idempotency_key
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_policy_rejects_download_for_course_content_before_database() -> None:
    asset_id = uuid4()
    auth = FakeAuth(seller=SELLER, scalar_values=[])
    body = json.dumps(
        {
            "idempotency_key": str(uuid4()),
            "expected_course_version": 1,
            "content_role": "course_content",
            "download_allowed": True,
        }
    ).encode()
    async with client_for(auth, FakeStorage(uuid4())) as client:
        response = await client.patch(
            asset_path(f"/{asset_id}/policy"),
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    assert auth.session.calls == []


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
async def test_video_upload_intent_is_fail_closed_until_rollout_flag_is_enabled() -> None:
    auth = FakeAuth(seller=SELLER, scalar_values=[])
    storage = FakeStorage(uuid4())
    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path("/upload-intents"),
            content=intent_body(media_type="video/mp4", file_name="aula.mp4"),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "video_upload_disabled"}
    assert auth.session.calls == []
    assert storage.presign_calls == []


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
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
async def test_storage_unavailable_consumes_authenticated_request() -> None:
    auth = FakeAuth(seller=SELLER, scalar_values=[])
    async with client_for(auth, None) as client:
        response = await client.post(
            asset_path("/upload-intents"),
            content=intent_body(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "asset_storage_unavailable"}
    assert auth.rejected_requests == 1
    assert auth.session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        intent_body(byte_size=max_bytes(PDF_MEDIA_TYPE) + 1),
        intent_body(file_name="../secret.pdf"),
        intent_body(media_type="text/html"),
        intent_body(media_type="video/mp4", file_name="material.pdf"),
        intent_body(media_type="application/pdf", file_name="material.mp4"),
        intent_body(
            media_type="video/mp4",
            file_name="material.mp4",
            byte_size=100 * 1024 * 1024 + 1,
        ),
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


# --- envio em partes ---------------------------------------------------------

VIDEO_GRANDE = MULTIPART_PART_BYTES + 1024


def _auth_para_envio_grande(
    upload_id: UUID, *, com_intencao: bool = True, extras: list[Any] | None = None
) -> FakeAuth:
    """A intenção criada e a leitura para assinatura, com tamanho de aula longa.

    `com_intencao=False` para as rotas que só leem a intenção existente.
    """
    assinatura = {
        "upload_id": upload_id,
        "bucket": "appsell-quarantine",
        "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
        "content_type": "video/mp4",
        "byte_size": VIDEO_GRANDE,
        "sha256": "ab" * 32,
        "expires_at": datetime.now(UTC) + timedelta(minutes=15),
    }
    criacao = (
        [{"upload_id": upload_id, "asset_id": uuid4(), "blob_id": uuid4(), "created": True}]
        if com_intencao
        else []
    )
    return FakeAuth(seller=SELLER, scalar_values=[*criacao, assinatura, *(extras or [])])


def _corpo_de_video_grande() -> bytes:
    return intent_body(media_type="video/mp4", file_name="aula-longa.mp4", byte_size=VIDEO_GRANDE)


@pytest.mark.asyncio
async def test_a_aula_longa_recebe_um_plano_de_partes_em_vez_de_uma_url() -> None:
    """Mandar uma URL única aqui seria oferecer um caminho que não fecha.

    Numa linha de 10 Mbit/s este arquivo não termina dentro do prazo da
    assinatura, e o vendedor perderia o envio inteiro no fim.
    """
    upload_id = uuid4()
    auth = _auth_para_envio_grande(upload_id)
    storage = FakeStorage(upload_id)
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.post(
            asset_path("/upload-intents"),
            content=_corpo_de_video_grande(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 201
    payload = response.json()
    assert "upload_url" not in payload
    assert payload["multipart"] == {
        "part_bytes": MULTIPART_PART_BYTES,
        "part_count": 2,
    }
    assert [nome for nome, _ in storage.multipart_calls] == ["find", "create"]
    assert storage.presign_calls == []


@pytest.mark.asyncio
async def test_uma_intencao_repetida_nao_abre_um_segundo_envio() -> None:
    """Dois envios abertos para a mesma chave é estado que ninguém sabe explicar.

    Fechar o errado montaria o arquivo com as partes de outra tentativa.
    """
    upload_id = uuid4()
    auth = _auth_para_envio_grande(upload_id)
    storage = FakeStorage(upload_id)
    storage.open_multipart_id = "envio-que-ja-existia"
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.post(
            asset_path("/upload-intents"),
            content=_corpo_de_video_grande(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 201
    assert [nome for nome, _ in storage.multipart_calls] == ["find"]


@pytest.mark.asyncio
async def test_a_parte_e_assinada_com_o_tamanho_do_plano() -> None:
    upload_id = uuid4()
    auth = _auth_para_envio_grande(upload_id, com_intencao=False)
    storage = FakeStorage(upload_id)
    storage.open_multipart_id = "envio-aberto"
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/parts/2"),
            content=json.dumps({"sha256": "cd" * 32}).encode(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["part_number"] == 2
    # A segunda parte deste arquivo é o resto, e não uma parte inteira.
    assert payload["required_content_length"] == 1024
    assert payload["required_headers"]["x-amz-checksum-sha256"] == "checksum-da-parte"
    assinatura = next(kwargs for nome, kwargs in storage.multipart_calls if nome == "sign")
    assert assinatura["sha256_hex"] == "cd" * 32
    assert auth.authenticated_capabilities == ["catalog.asset.upload.sign"]


@pytest.mark.asyncio
async def test_assinar_parte_sem_envio_aberto_e_conflito() -> None:
    """Sem envio aberto não há o que assinar, e inventar um perderia as partes."""
    upload_id = uuid4()
    auth = _auth_para_envio_grande(upload_id, com_intencao=False)
    storage = FakeStorage(upload_id)
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/parts/1"),
            content=json.dumps({"sha256": "cd" * 32}).encode(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "asset_upload_not_open"


@pytest.mark.asyncio
async def test_a_listagem_mostra_o_que_ja_subiu_para_permitir_retomar() -> None:
    upload_id = uuid4()
    auth = _auth_para_envio_grande(upload_id, com_intencao=False)
    storage = FakeStorage(upload_id)
    storage.open_multipart_id = "envio-aberto"
    storage.uploaded_parts = (
        UploadedPart(part_number=1, byte_size=MULTIPART_PART_BYTES, etag="a"),
    )
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.get(asset_path(f"/uploads/{upload_id}/parts"))

    assert response.status_code == 200
    assert response.json() == {
        "part_bytes": MULTIPART_PART_BYTES,
        "part_count": 2,
        "uploaded_parts": [{"part_number": 1, "byte_size": MULTIPART_PART_BYTES}],
    }


@pytest.mark.asyncio
async def test_um_envio_pequeno_nao_tem_partes_para_listar() -> None:
    """Pedir partes de um envio que não é fatiado é pergunta sem resposta."""
    upload_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "application/pdf",
                "byte_size": 128,
                "sha256": "ab" * 32,
                "expires_at": datetime.now(UTC) + timedelta(minutes=15),
            }
        ],
    )
    async with client_for(auth, FakeStorage(upload_id)) as client:
        response = await client.get(asset_path(f"/uploads/{upload_id}/parts"))

    assert response.status_code == 409
    assert response.json()["detail"] == "asset_upload_is_not_multipart"


@pytest.mark.asyncio
async def test_confirmar_fecha_o_envio_e_adia_a_conferencia_de_conteudo() -> None:
    """O envio fatiado não tem hash de arquivo inteiro para conferir aqui.

    O storage guarda um hash composto das partes, e o banco sabe disso pelo
    tamanho declarado: a conferência de conteúdo vira a da verificação, que
    relê todos os bytes contra o mesmo hash da intenção.
    """
    upload_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            None,
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "video/mp4",
                "byte_size": VIDEO_GRANDE,
                "sha256": "ab" * 32,
            },
            {"upload_id": upload_id, "status": "uploaded", "confirmed": True},
        ],
    )
    storage = FakeStorage(upload_id)
    storage.observed_size = VIDEO_GRANDE
    # O que o storage reporta para um objeto montado de partes: composto, com o
    # número de partes no fim. Não é um sha256 e não pode ser tratado como um.
    storage.observed_checksum_b64 = "Y29tcG9zdG8=-2"
    storage.open_multipart_id = "envio-aberto"
    storage.uploaded_parts = (
        UploadedPart(part_number=1, byte_size=MULTIPART_PART_BYTES, etag="a"),
        UploadedPart(part_number=2, byte_size=1024, etag="b"),
    )
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/confirm"),
            content=json.dumps({"idempotency_key": str(uuid4())}).encode(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 202
    assert [nome for nome, _ in storage.multipart_calls] == ["find", "list", "complete"]
    _, parameters = auth.session.calls[2]
    assert parameters["checksum"] is None
    assert parameters["size"] == VIDEO_GRANDE


@pytest.mark.asyncio
async def test_confirmar_de_novo_nao_precisa_de_envio_aberto() -> None:
    """Uma confirmação repetida chega depois de o envio já ter fechado.

    Não achar envio aberto não é erro: quem diz se o objeto existe é o `HEAD`.
    """
    upload_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            None,
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "video/mp4",
                "byte_size": VIDEO_GRANDE,
                "sha256": "ab" * 32,
            },
            {"upload_id": upload_id, "status": "uploaded", "confirmed": True},
        ],
    )
    storage = FakeStorage(upload_id)
    storage.observed_size = VIDEO_GRANDE
    storage.observed_checksum_b64 = "Y29tcG9zdG8=-2"
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/confirm"),
            content=json.dumps({"idempotency_key": str(uuid4())}).encode(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 202
    assert [nome for nome, _ in storage.multipart_calls] == ["find"]


@pytest.mark.asyncio
async def test_cancelar_apaga_as_partes_ja_enviadas() -> None:
    """Cancelar sem abortar deixa as partes ocupando disco sem aparecer em nada."""
    upload_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "video/mp4",
                "byte_size": VIDEO_GRANDE,
                "sha256": "ab" * 32,
            },
            {"upload_id": upload_id, "status": "cancelled"},
        ],
    )
    storage = FakeStorage(upload_id)
    storage.open_multipart_id = "envio-aberto"
    async with client_for(auth, storage, video_upload_enabled=True) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/cancel"),
            content=json.dumps({"idempotency_key": str(uuid4())}).encode(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200
    assert [nome for nome, _ in storage.multipart_calls] == ["find", "abort"]
    # A ordem importa: o banco autoriza e decide primeiro; só depois as partes
    # são apagadas. O contrário destruiria um envio legítimo se o banco recusasse.
    assert "cancel_asset_upload" in auth.session.calls[1][0]


@pytest.mark.asyncio
async def test_cancelar_envio_pequeno_nao_procura_partes() -> None:
    upload_id = uuid4()
    auth = FakeAuth(
        seller=SELLER,
        scalar_values=[
            {
                "upload_id": upload_id,
                "bucket": "appsell-quarantine",
                "object_key": f"tenants/{SELLER}/quarantine/{upload_id}",
                "content_type": "application/pdf",
                "byte_size": 128,
                "sha256": "ab" * 32,
            },
            {"upload_id": upload_id, "status": "cancelled"},
        ],
    )
    storage = FakeStorage(upload_id)
    async with client_for(auth, storage) as client:
        response = await client.post(
            asset_path(f"/uploads/{upload_id}/cancel"),
            content=json.dumps({"idempotency_key": str(uuid4())}).encode(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200
    assert storage.multipart_calls == []
