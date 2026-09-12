from __future__ import annotations

import base64
import binascii
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi import Path as PathParameter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .asset_media import (
    MAX_ASSET_BYTES,
    PDF_MEDIA_TYPE,
    VIDEO_MEDIA_TYPE,
    extension_matches,
    spec_for,
)
from .asset_media import (
    max_bytes as _catalogue_max_bytes,
)
from .asset_media import max_bytes as asset_max_bytes
from .asset_worker_runner import process_asset_upload
from .catalog_routes import (
    _authenticate,
    _canonical_uuid,
    _contains_control,
    _domain_error,
    _model_from_body,
    _require_seller,
)
from .internal_requests import AuthenticatedInternalRequest
from .logging_setup import get_logger
from .storage_s3 import (
    MULTIPART_MAX_PARTS,
    S3Storage,
    StorageError,
    multipart_plan,
    needs_multipart,
)

router = APIRouter(prefix="/v1")
_logger = get_logger(__name__)
_MAX_SAFE_INTEGER = (1 << 53) - 1
_MAX_PDF_BYTES = _catalogue_max_bytes(PDF_MEDIA_TYPE)
_MAX_VIDEO_BYTES = _catalogue_max_bytes(VIDEO_MEDIA_TYPE)


class AssetUploadIntentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_course_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    existing_asset_id: str | None = None
    title: str
    # Validated against the shared catalogue rather than restated here, so a
    # format accepted by the database is never refused by the request model.
    media_type: str
    file_name: str
    byte_size: int = Field(ge=1, le=MAX_ASSET_BYTES)
    sha256: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")

    @field_validator("existing_asset_id")
    @classmethod
    def validate_asset_id(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_uuid(value, "invalid asset id")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 2 <= len(value.encode("utf-8")) <= 160
            or _contains_control(value)
        ):
            raise ValueError("invalid asset title")
        return value

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 1 <= len(value.encode("utf-8")) <= 160
            or _contains_control(value)
            or "/" in value
            or "\\" in value
            or "." not in value
        ):
            raise ValueError("invalid asset file name")
        return value

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if spec_for(value) is None:
            raise ValueError("unsupported asset media type")
        return value

    @field_validator("byte_size")
    @classmethod
    def validate_media_size(cls, value: int, info: Any) -> int:
        media_type = info.data.get("media_type")
        if not isinstance(media_type, str) or value > asset_max_bytes(media_type):
            raise ValueError("asset exceeds media size limit")
        return value

    @field_validator("file_name")
    @classmethod
    def validate_media_extension(cls, value: str, info: Any) -> str:
        media_type = info.data.get("media_type")
        if not isinstance(media_type, str) or not extension_matches(media_type, value):
            raise ValueError("asset extension does not match MIME")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("invalid SHA-256")
        return value


class AssetUploadPartBody(BaseModel):
    """O hash da parte, que entra na assinatura e vira recusa no storage."""

    model_config = ConfigDict(extra="forbid", strict=True)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must be lowercase hexadecimal")
        return value


class AssetMutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idempotency_key: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


class AssetArchiveBody(AssetMutationBody):
    expected_course_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)


class AssetPolicyBody(AssetArchiveBody):
    content_role: Literal["course_content", "support_material"]
    download_allowed: bool

    @field_validator("download_allowed")
    @classmethod
    def validate_download_policy(cls, value: bool, info: Any) -> bool:
        if value and info.data.get("content_role") != "support_material":
            raise ValueError("download requires support material")
        return value


@router.get(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets"
)
async def list_lesson_assets(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.asset.read")
    await _require_seller(internal, seller_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select "
                            "app_private.list_lesson_asset_drafts_v2("
                            ":actor,:seller,:product,:course,:module,:lesson) assets, "
                            "app_private.read_lesson_asset_uploads("
                            ":actor,:seller,:product,:course,:module,:lesson) uploads"
                        ),
                        _route_parameters(
                            internal.envelope.identity.user_id,
                            seller_id,
                            product_id,
                            course_id,
                            module_id,
                            lesson_id,
                        ),
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "assets": [_asset_view(value) for value in _json_list(row["assets"])],
        "uploads": [_upload_view(value) for value in _json_list(row["uploads"])],
    }


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/upload-intents",
    response_model=None,
)
async def create_asset_upload_intent(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.asset.create")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AssetUploadIntentBody, internal)
    if body.media_type == "video/mp4" and not request.app.state.settings.video_upload_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="video_upload_disabled")
    storage = await _storage(request, internal)
    settings = request.app.state.settings
    parameters = _route_parameters(
        internal.envelope.identity.user_id,
        seller_id,
        product_id,
        course_id,
        module_id,
        lesson_id,
    )
    parameters.update(
        {
            "existing_asset": UUID(body.existing_asset_id) if body.existing_asset_id else None,
            "expected_course_version": body.expected_course_version,
            "title": body.title,
            "file_name": body.file_name,
            "media_type": body.media_type,
            "byte_size": body.byte_size,
            "sha256": body.sha256,
            "storage_driver": "s3",
            "quarantine_bucket": settings.storage_quarantine_bucket,
            "ready_bucket": settings.storage_ready_bucket,
            "idempotency_key": UUID(body.idempotency_key),
            "body_sha256": internal.envelope.body_sha256,
            "request_id": internal.envelope.request_id,
        }
    )
    try:
        async with auth.domain_session(internal.envelope) as session:
            result = await session.scalar(
                text(
                    "select app_private.create_asset_upload_intent("
                    ":actor,:seller,:product,:course,:module,:lesson,:existing_asset,"
                    ":expected_course_version,:title,:file_name,:media_type,:byte_size,"
                    ":sha256,:storage_driver,:quarantine_bucket,:ready_bucket,"
                    ":idempotency_key,:body_sha256,:request_id)"
                ),
                parameters,
            )
        upload_id = UUID(str(_json_object(result)["upload_id"]))
        signing = await _read_upload_for_signing(
            auth=auth,
            envelope=internal.envelope,
            seller_id=seller_id,
            product_id=product_id,
            course_id=course_id,
            module_id=module_id,
            lesson_id=lesson_id,
            upload_id=upload_id,
        )
        if needs_multipart(int(signing["byte_size"])):
            plan = multipart_plan(int(signing["byte_size"]))
            # Uma intenção repetida não pode abrir um segundo envio para a mesma
            # chave: dois envios abertos são estado que ninguém sabe explicar, e
            # fechar o errado montaria o arquivo com as partes de outra
            # tentativa.
            storage_upload_id = await to_thread.run_sync(
                lambda: storage.find_multipart_upload(
                    bucket=str(signing["bucket"]), key=str(signing["object_key"])
                )
            )
            if storage_upload_id is None:
                await to_thread.run_sync(
                    lambda: storage.create_multipart_upload(
                        bucket=str(signing["bucket"]),
                        key=str(signing["object_key"]),
                        content_type=str(signing["content_type"]),
                        byte_size=int(signing["byte_size"]),
                        upload_request_id=str(upload_id),
                    )
                )
            signed = None
            # A validade que o navegador recebe é a **durável**, e não a de uma
            # assinatura: no envio fatiado ele não recebe URL nenhuma aqui, e
            # cada parte traz o próprio prazo curto. Cortar em 300 s diria ao
            # vendedor que ele tem cinco minutos para mandar meio giga.
            signed_expires_at = _durable_expiry(signing["expires_at"])
        else:
            plan = None
            signed = await to_thread.run_sync(
                lambda: storage.presign_put(
                    bucket=str(signing["bucket"]),
                    key=str(signing["object_key"]),
                    content_type=str(signing["content_type"]),
                    sha256_hex=str(signing["sha256"]),
                    byte_size=int(signing["byte_size"]),
                    upload_request_id=str(upload_id),
                )
            )
            signed_expires_at = _bounded_presigned_expiry(
                signing["expires_at"], signed.expires_in_seconds
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="asset_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    except (StorageError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_unavailable",
        ) from error
    result_object = _json_object(result)
    content: dict[str, Any] = {
        "upload_id": str(upload_id),
        "asset_id": str(result_object["asset_id"]),
        # The browser must receive the shorter presigned-request lifetime,
        # not the 15-minute durable intent lifetime stored in Postgres.
        "expires_at": _iso_datetime(signed_expires_at),
        "created": result_object.get("created", True),
    }
    if plan is not None:
        # Sem `upload_url`: o envio fatiado pede uma URL por parte, e cada uma
        # dura pouco de propósito. Mandar uma aqui seria oferecer um caminho que
        # não fecha para este arquivo.
        content["multipart"] = {"part_bytes": plan.part_bytes, "part_count": plan.part_count}
    elif signed is not None:
        content["upload_url"] = signed.url
        content["required_headers"] = signed.headers
        content["required_content_length"] = signed.required_content_length
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if result_object.get("created") is not False else 200,
        content=content,
        headers={"cache-control": "no-store", "referrer-policy": "no-referrer"},
    )


@router.get(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/uploads/{upload_id}"
)
async def read_asset_upload_status(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    upload_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.asset.read")
    await _require_seller(internal, seller_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.read_asset_upload_status("
                    ":actor,:seller,:product,:course,:module,:lesson,:upload)"
                ),
                {
                    **_route_parameters(
                        internal.envelope.identity.user_id,
                        seller_id,
                        product_id,
                        course_id,
                        module_id,
                        lesson_id,
                    ),
                    "upload": upload_id,
                },
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return _status_view(_json_object(value))


@router.get(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/uploads/{upload_id}/parts"
)
async def read_asset_upload_parts(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    upload_id: UUID,
) -> dict[str, Any]:
    """O que já chegou, que é o que permite retomar em vez de recomeçar.

    Sem esta resposta o navegador não teria como saber o que sobreviveu a uma
    queda de rede, e a única saída honesta seria mandar tudo de novo.
    """
    auth, internal = await _authenticate(request, "catalog.asset.read")
    await _require_seller(internal, seller_id)
    storage = await _storage(request, internal)
    signing, plan = await _multipart_context(
        auth=auth,
        envelope=internal.envelope,
        seller_id=seller_id,
        product_id=product_id,
        course_id=course_id,
        module_id=module_id,
        lesson_id=lesson_id,
        upload_id=upload_id,
    )
    try:
        storage_upload_id = await to_thread.run_sync(
            lambda: storage.find_multipart_upload(
                bucket=str(signing["bucket"]), key=str(signing["object_key"])
            )
        )
        enviadas = (
            ()
            if storage_upload_id is None
            else await to_thread.run_sync(
                lambda: storage.list_multipart_parts(
                    bucket=str(signing["bucket"]),
                    key=str(signing["object_key"]),
                    storage_upload_id=str(storage_upload_id),
                )
            )
        )
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_unavailable",
        ) from error
    return {
        "part_bytes": plan.part_bytes,
        "part_count": plan.part_count,
        "uploaded_parts": [
            {"part_number": parte.part_number, "byte_size": parte.byte_size} for parte in enviadas
        ],
    }


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/uploads/{upload_id}"
    "/parts/{part_number}",
    response_model=None,
)
async def sign_asset_upload_part(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    upload_id: UUID,
    part_number: int = PathParameter(ge=1, le=MULTIPART_MAX_PARTS),
) -> JSONResponse:
    """A URL de uma parte, com o tamanho e o hash dela presos na assinatura.

    Uma URL por parte, e não uma para o arquivo todo: cada uma dura pouco, e é
    isso que permite um envio longo sobreviver ao prazo da assinatura.
    """
    auth, internal = await _authenticate(request, "catalog.asset.upload.sign")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AssetUploadPartBody, internal)
    storage = await _storage(request, internal)
    signing, plan = await _multipart_context(
        auth=auth,
        envelope=internal.envelope,
        seller_id=seller_id,
        product_id=product_id,
        course_id=course_id,
        module_id=module_id,
        lesson_id=lesson_id,
        upload_id=upload_id,
    )
    try:
        storage_upload_id = await to_thread.run_sync(
            lambda: storage.find_multipart_upload(
                bucket=str(signing["bucket"]), key=str(signing["object_key"])
            )
        )
        if storage_upload_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="asset_upload_not_open"
            )
        signed = await to_thread.run_sync(
            lambda: storage.presign_upload_part(
                bucket=str(signing["bucket"]),
                key=str(signing["object_key"]),
                storage_upload_id=str(storage_upload_id),
                part_number=part_number,
                plan=plan,
                sha256_hex=body.sha256,
            )
        )
    except HTTPException:
        raise
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_unavailable",
        ) from error
    return JSONResponse(
        content={
            "part_number": part_number,
            "upload_url": signed.url,
            "required_headers": signed.headers,
            "required_content_length": signed.required_content_length,
            "expires_at": _iso_datetime(
                _bounded_presigned_expiry(signing["expires_at"], signed.expires_in_seconds)
            ),
        },
        headers={"cache-control": "no-store", "referrer-policy": "no-referrer"},
    )


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/uploads/{upload_id}/confirm",
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_asset_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    upload_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.asset.upload.confirm")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AssetMutationBody, internal)
    storage = await _storage(request, internal)
    replay_parameters = {
        **_route_parameters(
            internal.envelope.identity.user_id,
            seller_id,
            product_id,
            course_id,
            module_id,
            lesson_id,
        ),
        "upload": upload_id,
        "idempotency_key": UUID(body.idempotency_key),
        "body_sha256": internal.envelope.body_sha256,
    }
    try:
        # Check the route-bound receipt before asking storage for the source object.
        # This makes a lost 202 response replayable after the upload has advanced
        # from pending to uploaded, verifying, ready, or rejected.
        async with auth.domain_session(internal.envelope) as session:
            replay = await session.scalar(
                text(
                    "select app_private.replay_asset_upload_confirmation("
                    ":actor,:seller,:product,:course,:module,:lesson,:upload,"
                    ":idempotency_key,:body_sha256)"
                ),
                replay_parameters,
            )
        if replay is not None:
            return _json_object(replay)
        signing = await _read_upload_for_signing(
            auth=auth,
            envelope=internal.envelope,
            seller_id=seller_id,
            product_id=product_id,
            course_id=course_id,
            module_id=module_id,
            lesson_id=lesson_id,
            upload_id=upload_id,
        )
        fatiado = needs_multipart(int(signing["byte_size"]))
        if fatiado:
            await _fecha_envio_em_partes(storage, signing)
        observed = await to_thread.run_sync(
            lambda: storage.head(bucket=str(signing["bucket"]), key=str(signing["object_key"]))
        )
        if getattr(observed, "appsell_upload_id", "") != str(upload_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="asset_upload_metadata_mismatch",
            )
        # Um envio fatiado não carrega hash do arquivo inteiro: o storage guarda
        # um hash composto das partes. O banco sabe disso pelo tamanho declarado
        # e adia a conferência de conteúdo para a verificação, que relê todos os
        # bytes contra o mesmo hash da intenção.
        checksum_hex = None if fatiado else _checksum_hex(observed.checksum_sha256_b64)
        async with auth.continuation_domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.confirm_asset_upload("
                    ":actor,:seller,:product,:course,:module,:lesson,:upload,"
                    ":version_id,:size,:checksum,:etag,:idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    **_route_parameters(
                        internal.envelope.identity.user_id,
                        seller_id,
                        product_id,
                        course_id,
                        module_id,
                        lesson_id,
                    ),
                    "upload": upload_id,
                    "version_id": observed.version_id,
                    "size": observed.size,
                    "checksum": checksum_hex,
                    "etag": observed.etag,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
    except HTTPException:
        raise
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="asset_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    except (StorageError, ValueError, binascii.Error) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_unavailable",
        ) from error
    result = _json_object(value)
    if result.get("status") == "uploaded" and getattr(
        request.app.state, "inline_asset_worker_enabled", False
    ):
        background_tasks.add_task(process_asset_upload, request.app, seller_id, upload_id)
    return result


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/uploads/{upload_id}/cancel"
)
async def cancel_asset_upload(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    upload_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.asset.upload.cancel")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AssetMutationBody, internal)
    # Lido antes do cancelamento, enquanto a intenção ainda existe para ser
    # lida. A ordem importa: o banco é quem autoriza, então ele decide primeiro
    # e só depois as partes são apagadas.
    signing = await _read_upload_for_signing(
        auth=auth,
        envelope=internal.envelope,
        seller_id=seller_id,
        product_id=product_id,
        course_id=course_id,
        module_id=module_id,
        lesson_id=lesson_id,
        upload_id=upload_id,
    )
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.cancel_asset_upload("
                    ":actor,:seller,:product,:course,:module,:lesson,:upload,"
                    ":idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    **_route_parameters(
                        internal.envelope.identity.user_id,
                        seller_id,
                        product_id,
                        course_id,
                        module_id,
                        lesson_id,
                    ),
                    "upload": upload_id,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="asset_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    if needs_multipart(int(signing["byte_size"])):
        # Cancelar sem abortar deixaria as partes gravadas ocupando disco sem
        # aparecer em listagem de objeto nenhuma. Falhar aqui não desfaz o
        # cancelamento, que já está gravado: a regra de expiração do bucket é a
        # rede embaixo desta.
        try:
            storage = await _storage(request, internal)
            storage_upload_id = await to_thread.run_sync(
                lambda: storage.find_multipart_upload(
                    bucket=str(signing["bucket"]), key=str(signing["object_key"])
                )
            )
            if storage_upload_id is not None:
                await to_thread.run_sync(
                    lambda: storage.abort_multipart_upload(
                        bucket=str(signing["bucket"]),
                        key=str(signing["object_key"]),
                        storage_upload_id=str(storage_upload_id),
                    )
                )
        except (StorageError, HTTPException) as error:
            _logger.warning(
                "appsell_multipart_abort_failed",
                upload_id=str(upload_id),
                reason=type(error).__name__,
            )
    return _json_object(value)


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/{asset_id}/archive"
)
async def archive_lesson_asset(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    asset_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.asset.archive")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AssetArchiveBody, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.archive_lesson_asset("
                    ":actor,:seller,:product,:course,:module,:lesson,:asset,"
                    ":expected_course_version,:idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    **_route_parameters(
                        internal.envelope.identity.user_id,
                        seller_id,
                        product_id,
                        course_id,
                        module_id,
                        lesson_id,
                    ),
                    "asset": asset_id,
                    "expected_course_version": body.expected_course_version,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="asset_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    return _json_object(value)


@router.patch(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/assets/{asset_id}/policy"
)
async def update_lesson_asset_policy(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    asset_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.asset.policy.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AssetPolicyBody, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.set_lesson_asset_policy("
                    ":actor,:seller,:product,:course,:module,:lesson,:asset,"
                    ":content_role,:download_allowed,:expected_course_version,"
                    ":idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    **_route_parameters(
                        internal.envelope.identity.user_id,
                        seller_id,
                        product_id,
                        course_id,
                        module_id,
                        lesson_id,
                    ),
                    "asset": asset_id,
                    "content_role": body.content_role,
                    "download_allowed": body.download_allowed,
                    "expected_course_version": body.expected_course_version,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="asset_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    return _json_object(value)


async def _fecha_envio_em_partes(storage: S3Storage, signing: dict[str, Any]) -> None:
    """Monta o objeto a partir das partes, se ainda houver envio aberto.

    Não achar envio aberto não é erro: uma confirmação repetida chega depois de
    o envio já ter fechado. Quem diz se o objeto existe é o `HEAD` logo abaixo,
    e é ele que precisa continuar sendo a resposta.
    """
    storage_upload_id = await to_thread.run_sync(
        lambda: storage.find_multipart_upload(
            bucket=str(signing["bucket"]), key=str(signing["object_key"])
        )
    )
    if storage_upload_id is None:
        return
    plan = multipart_plan(int(signing["byte_size"]))
    partes = await to_thread.run_sync(
        lambda: storage.list_multipart_parts(
            bucket=str(signing["bucket"]),
            key=str(signing["object_key"]),
            storage_upload_id=str(storage_upload_id),
        )
    )
    await to_thread.run_sync(
        lambda: storage.complete_multipart_upload(
            bucket=str(signing["bucket"]),
            key=str(signing["object_key"]),
            storage_upload_id=str(storage_upload_id),
            plan=plan,
            parts=partes,
        )
    )


async def _multipart_context(
    *,
    auth: Any,
    envelope: Any,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    upload_id: UUID,
) -> tuple[dict[str, Any], Any]:
    """A intenção e o plano de partes dela, recusando quem não é fatiado.

    O plano é derivado do tamanho gravado na intenção, e não de nada que venha
    no pedido: é o mesmo número dos dois lados, e não dá para pedir um plano
    diferente do que foi declarado.
    """
    signing = await _read_upload_for_signing(
        auth=auth,
        envelope=envelope,
        seller_id=seller_id,
        product_id=product_id,
        course_id=course_id,
        module_id=module_id,
        lesson_id=lesson_id,
        upload_id=upload_id,
    )
    if not needs_multipart(int(signing["byte_size"])):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="asset_upload_is_not_multipart"
        )
    try:
        return signing, multipart_plan(int(signing["byte_size"]))
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="asset_storage_unavailable"
        ) from error


async def _read_upload_for_signing(
    *,
    auth: Any,
    envelope: Any,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    upload_id: UUID,
) -> dict[str, Any]:
    signing_envelope = replace(envelope, capability="catalog.asset.upload.sign")
    async with auth.continuation_domain_session(signing_envelope) as session:
        value = await session.scalar(
            text(
                "select app_private.read_asset_upload_for_signing("
                ":actor,:seller,:product,:course,:module,:lesson,:upload)"
            ),
            {
                **_route_parameters(
                    envelope.identity.user_id,
                    seller_id,
                    product_id,
                    course_id,
                    module_id,
                    lesson_id,
                ),
                "upload": upload_id,
            },
        )
    return _json_object(value)


def _route_parameters(
    actor: UUID,
    seller: UUID,
    product: UUID,
    course: UUID,
    module: UUID,
    lesson: UUID,
) -> dict[str, Any]:
    return {
        "actor": actor,
        "seller": seller,
        "product": product,
        "course": course,
        "module": module,
        "lesson": lesson,
    }


async def _storage(request: Request, internal: AuthenticatedInternalRequest) -> S3Storage:
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    # Ter o objeto não é o mesmo que poder aceitar arquivo. Desde 12/09/2026 a
    # API sobe com a dependência degradada em vez de entrar em laço de reinício
    # e levar a loja junto, e o monitor precisa do objeto em mãos para reavaliar
    # e recuperar sozinho — então a presença dele deixou de ser prova de nada.
    # Sem o antivírus, o que entrasse aqui ficaria parado na quarentena à espera
    # de um worker que não vai processá-lo: melhor a recusa na porta.
    degradado = not bool(getattr(request.app.state, "storage_runtime_ready", False))
    if storage is None or degradado:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_unavailable",
        )
    return storage


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="invalid_asset_response",
        )
    return value


def _json_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(entry, dict) for entry in value):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="invalid_asset_response",
        )
    return value


def _asset_view(value: dict[str, Any]) -> dict[str, Any]:
    draft_status = str(value["status"])
    return {
        "id": str(value["asset_id"]),
        "title": str(value["title"]),
        "file_name": str(value["file_name"]),
        "byte_size": int(value["byte_size"]),
        "media_type": str(value["media_type"]),
        "status": "ready" if draft_status == "active" else draft_status,
        "content_role": str(value.get("content_role", "course_content")),
        "download_allowed": bool(value.get("download_allowed", False)),
    }


def _upload_view(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(value["upload_id"]),
        "status": str(value["status"]),
        "file_name": str(value["file_name"]),
        "byte_size": int(value["byte_size"]),
        "safe_reason": _safe_reason(value.get("safe_reason")),
    }


def _status_view(value: dict[str, Any]) -> dict[str, Any]:
    ready = value.get("ready_asset")
    return {
        "upload_id": str(value["upload_id"]),
        "status": str(value["status"]),
        "safe_reason": _safe_reason(value.get("safe_reason")),
        "retry_after_seconds": 2,
        "asset": _asset_view({**ready, "status": "ready"}) if isinstance(ready, dict) else None,
    }


def _safe_reason(value: Any) -> str | None:
    messages = {
        "expired": "O prazo do envio terminou. Escolha o PDF novamente.",
        "upload_mismatch": "O arquivo recebido não corresponde ao PDF selecionado.",
        "verification_failed": "Este arquivo não passou na verificação. Escolha outro PDF.",
        "cancelled": "O envio foi cancelado. Seu curso não foi alterado.",
    }
    return messages.get(str(value)) if value is not None else None


def _checksum_hex(value: str) -> str:
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != 32:
        raise ValueError("invalid storage checksum")
    return decoded.hex()


def _iso_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _durable_expiry(intent_expiry: Any) -> datetime:
    """A validade da intenção como o banco a gravou, sem cortar."""
    if isinstance(intent_expiry, datetime):
        durable = intent_expiry
    elif isinstance(intent_expiry, str):
        durable = datetime.fromisoformat(intent_expiry.replace("Z", "+00:00"))
    else:
        raise ValueError("asset intent expiry is invalid")
    if durable.tzinfo is None:
        raise ValueError("asset intent expiry must be timezone-aware")
    return durable.astimezone(UTC)


def _bounded_presigned_expiry(intent_expiry: Any, ttl_seconds: int) -> datetime:
    if ttl_seconds < 1:
        raise ValueError("presigned request TTL must be positive")
    if isinstance(intent_expiry, datetime):
        durable_expiry = intent_expiry
    elif isinstance(intent_expiry, str):
        durable_expiry = datetime.fromisoformat(intent_expiry.replace("Z", "+00:00"))
    else:
        raise ValueError("asset intent expiry is invalid")
    if durable_expiry.tzinfo is None:
        raise ValueError("asset intent expiry must be timezone-aware")
    signed_expiry = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    return min(durable_expiry.astimezone(UTC), signed_expiry)
