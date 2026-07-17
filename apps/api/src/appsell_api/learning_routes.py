from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .catalog_routes import _domain_error
from .config import Settings
from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator
from .learning_pdf import (
    LearningPdfRejected,
    LearningPdfUnavailable,
    authorized_pdf_source,
    inspect_pdf,
    render_pdf_page,
)
from .storage_s3 import S3Storage, StorageError

router = APIRouter(prefix="/v1")
_MAX_LEARNING_BODY_BYTES = 4_096
_MAX_SAFE_INTEGER = (1 << 53) - 1
_LEARNING_ASSETS_CONTRACT_HEADER = "x-appsell-learning-assets-contract"
_PROTECTED_PDF_SLOTS = asyncio.Semaphore(2)


class LessonProgressBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    progress_percent: int = Field(ge=0, le=100)
    expected_version: int = Field(ge=0, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


class LessonCompletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_version: int = Field(ge=0, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


async def _authenticate(
    request: Request, capability: str
) -> tuple[InternalRequestAuthenticator, AuthenticatedInternalRequest]:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_auth_not_configured",
        )
    return auth, await auth.authenticate(request, capability)


@router.get("/me/library")
async def read_library(request: Request) -> dict[str, Any]:
    return await _read_buyer_products(request)


@router.get("/me/products")
async def read_buyer_products(request: Request) -> dict[str, Any]:
    return await _read_buyer_products(request)


async def _read_buyer_products(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            products = await session.scalar(
                text("select app_private.read_buyer_products(:identity_user_id)"),
                {"identity_user_id": internal.envelope.identity.user_id},
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"items": products or []}


@router.get("/me/products/{entitlement_id}/seller-products")
async def read_buyer_seller_products(request: Request, entitlement_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            products = await session.scalar(
                text(
                    "select app_private.read_buyer_seller_storefront("
                    ":identity_user_id,:entitlement_id)"
                ),
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                },
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    return {"items": products or []}


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}")
async def authorize_asset(request: Request, entitlement_id: UUID, asset_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.asset.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select asset_id,asset_version,file_name,title,media_type,byte_size,"
                            "sha256,storage_driver,storage_bucket,object_key,storage_version_id,"
                            "storage_etag from app_private.authorize_publication_asset("
                            ":identity_user_id,:entitlement_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "entitlement_id": entitlement_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if row["storage_driver"] == "s3":
        storage: S3Storage | None = getattr(request.app.state, "storage", None)
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            )
        try:
            signed = await to_thread.run_sync(
                lambda: storage.presign_get(
                    bucket=str(row["storage_bucket"]),
                    key=str(row["object_key"]),
                    version_id=str(row["storage_version_id"]),
                    file_name=str(row["file_name"]),
                    media_type=str(row["media_type"]),
                    disposition="inline",
                )
            )
        except (StorageError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            ) from error
        return {
            "asset": {
                "id": str(row["asset_id"]),
                "delivery": "redirect",
                "download_url": signed.url,
                "expires_in_seconds": signed.expires_in_seconds,
                "file_name": row["file_name"],
                "title": row["title"],
                "media_type": row["media_type"],
                "byte_size": row["byte_size"],
                "sha256": row["sha256"],
            }
        }
    if row["storage_driver"] != "legacy_bundle":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_driver_unavailable",
        )
    return {
        "asset": {
            "id": str(row["asset_id"]),
            "delivery": "legacy_bundle",
            "seed_key": str(row["file_name"]).rsplit(".", 1)[0],
            "file_name": row["file_name"],
            "title": row["title"],
            "media_type": row["media_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
        }
    }


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/pages")
async def inspect_asset_pages(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> dict[str, Any]:
    internal, row = await _authorized_asset_row(request, entitlement_id, asset_id)
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_viewer_unavailable",
        )
    storage: S3Storage | None = getattr(request.app.state, "storage", None)

    def inspect() -> int:
        with authorized_pdf_source(row, settings, storage) as source:
            return inspect_pdf(source, settings)

    try:
        if _PROTECTED_PDF_SLOTS.locked():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="pdf_viewer_busy",
                headers={"retry-after": "2"},
            )
        async with _PROTECTED_PDF_SLOTS:
            page_count = await to_thread.run_sync(inspect)
    except LearningPdfRejected as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="protected_pdf_unavailable",
        ) from error
    except LearningPdfUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_viewer_unavailable",
        ) from error
    return {"asset": {"id": str(row["asset_id"]), "page_count": page_count}}


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/pages/{page_number}")
async def render_asset_page(
    request: Request,
    entitlement_id: UUID,
    asset_id: UUID,
    page_number: int,
) -> Response:
    internal, row = await _authorized_asset_row(request, entitlement_id, asset_id)
    if not 1 <= page_number <= 5_000:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="protected_pdf_unavailable",
        )
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_viewer_unavailable",
        )
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    watermark = (
        f"user {internal.envelope.identity.user_id.hex[:12]} "
        f"access {entitlement_id.hex[:12]}"
    )

    def render() -> Any:
        with authorized_pdf_source(row, settings, storage) as source:
            return render_pdf_page(
                source,
                settings,
                page_number=page_number,
                watermark=watermark,
            )

    try:
        if _PROTECTED_PDF_SLOTS.locked():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="pdf_viewer_busy",
                headers={"retry-after": "2"},
            )
        async with _PROTECTED_PDF_SLOTS:
            rendered = await to_thread.run_sync(render)
    except LearningPdfRejected as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="protected_pdf_unavailable",
        ) from error
    except LearningPdfUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_viewer_unavailable",
        ) from error
    return Response(
        rendered.payload,
        media_type="image/png",
        headers={
            "cache-control": "private, no-store, max-age=0",
            "content-disposition": f'inline; filename="page-{page_number}.png"',
            "content-security-policy": "sandbox",
            "cross-origin-resource-policy": "same-origin",
            "pragma": "no-cache",
            "referrer-policy": "no-referrer",
            "surrogate-control": "no-store",
            "vary": "x-appsell-internal-envelope",
            "x-appsell-page-count": str(rendered.page_count),
            "x-appsell-page-number": str(rendered.page_number),
            "x-appsell-sha256": rendered.sha256_hex,
            "x-content-type-options": "nosniff",
        },
    )


async def _authorized_asset_row(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> tuple[AuthenticatedInternalRequest, Any]:
    auth, internal = await _authenticate(request, "library.asset.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select asset_id,asset_version,file_name,title,media_type,byte_size,"
                            "sha256,storage_driver,storage_bucket,object_key,storage_version_id,"
                            "storage_etag from app_private.authorize_publication_asset("
                            ":identity_user_id,:entitlement_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "entitlement_id": entitlement_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    if row["media_type"] != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="protected_pdf_unavailable",
        )
    return internal, row


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/download")
async def authorize_asset_download(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.asset.download")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select asset_id,asset_version,file_name,title,media_type,byte_size,"
                            "sha256,storage_driver,storage_bucket,object_key,storage_version_id,"
                            "storage_etag from app_private.authorize_publication_asset_download("
                            ":identity_user_id,:entitlement_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "entitlement_id": entitlement_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    if row["storage_driver"] == "s3":
        storage: S3Storage | None = getattr(request.app.state, "storage", None)
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            )
        try:
            signed = await to_thread.run_sync(
                lambda: storage.presign_get(
                    bucket=str(row["storage_bucket"]),
                    key=str(row["object_key"]),
                    version_id=str(row["storage_version_id"]),
                    file_name=str(row["file_name"]),
                    media_type=str(row["media_type"]),
                    disposition="attachment",
                )
            )
        except (StorageError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            ) from error
        return {
            "asset": {
                "id": str(row["asset_id"]),
                "delivery": "redirect",
                "download_url": signed.url,
                "expires_in_seconds": signed.expires_in_seconds,
                "file_name": row["file_name"],
                "title": row["title"],
                "media_type": row["media_type"],
                "byte_size": row["byte_size"],
                "sha256": row["sha256"],
            }
        }
    if row["storage_driver"] != "legacy_bundle":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_driver_unavailable",
        )
    return {
        "asset": {
            "id": str(row["asset_id"]),
            "delivery": "legacy_bundle",
            "seed_key": str(row["file_name"]).rsplit(".", 1)[0],
            "file_name": row["file_name"],
            "title": row["title"],
            "media_type": row["media_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
        }
    }


@router.get("/me/learning/items/{entitlement_id}/resume")
async def read_learning_resume(request: Request, entitlement_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.player.read")
    await _require_buyer_scope(internal)
    player = await _read_player(
        auth,
        internal,
        entitlement_id,
        None,
        assets_contract=request.headers.get(_LEARNING_ASSETS_CONTRACT_HEADER),
    )
    return {"player": player}


@router.get("/me/learning/items/{entitlement_id}/lessons/{lesson_id}")
async def read_learning_lesson(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.player.read")
    await _require_buyer_scope(internal)
    player = await _read_player(
        auth,
        internal,
        entitlement_id,
        lesson_id,
        assets_contract=request.headers.get(_LEARNING_ASSETS_CONTRACT_HEADER),
    )
    return {"player": player}


@router.put("/me/learning/items/{entitlement_id}/lessons/{lesson_id}/progress")
async def update_learning_progress(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.progress.write")
    await _require_buyer_scope(internal)
    body = await _model_from_internal_body(request, internal, LessonProgressBody)
    result = await _record_progress(
        auth,
        internal,
        entitlement_id,
        lesson_id,
        body.progress_percent,
        body.expected_version,
        UUID(body.idempotency_key),
    )
    return {"progress": result}


@router.post("/me/learning/items/{entitlement_id}/lessons/{lesson_id}/complete")
async def complete_learning_lesson(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.progress.write")
    await _require_buyer_scope(internal)
    body = await _model_from_internal_body(request, internal, LessonCompletionBody)
    result = await _record_progress(
        auth,
        internal,
        entitlement_id,
        lesson_id,
        100,
        body.expected_version,
        UUID(body.idempotency_key),
    )
    return {"progress": result}


async def _read_player(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    entitlement_id: UUID,
    lesson_id: UUID | None,
    *,
    assets_contract: str | None = None,
) -> Any:
    if assets_contract == "v3":
        query = text(
            "select app_private.read_learning_player_v3("
            ":identity_user_id,:entitlement_id,:lesson_id)"
        )
    elif assets_contract == "v2":
        query = text(
            "select app_private.read_learning_player_v2("
            ":identity_user_id,:entitlement_id,:lesson_id)"
        )
    else:
        query = text(
            "select app_private.read_learning_player(:identity_user_id,:entitlement_id,:lesson_id)"
        )
    try:
        async with auth.domain_session(internal.envelope) as session:
            player = await session.scalar(
                query,
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                    "lesson_id": lesson_id,
                },
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    if player is None:
        raise _learning_not_found()
    return player


async def _record_progress(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    entitlement_id: UUID,
    lesson_id: UUID,
    progress_percent: int,
    expected_version: int,
    idempotency_key: UUID,
) -> Any:
    try:
        async with auth.domain_session(internal.envelope) as session:
            player = await session.scalar(
                text(
                    "select app_private.record_lesson_progress("
                    ":identity_user_id,:entitlement_id,:lesson_id,"
                    "cast(:progress_percent as smallint),"
                    ":expected_version,:idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                    "lesson_id": lesson_id,
                    "progress_percent": progress_percent,
                    "expected_version": expected_version,
                    "idempotency_key": idempotency_key,
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    if player is None:
        raise _learning_not_found()
    return player


async def _require_buyer_scope(internal: AuthenticatedInternalRequest) -> None:
    if internal.envelope.seller_organization_id is not None:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_internal_request",
        )


async def _model_from_internal_body(
    request: Request,
    internal: AuthenticatedInternalRequest,
    model: type[BaseModel],
) -> Any:
    if request.headers.get("content-type") != "application/json":
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="invalid_content_type",
        )
    if len(internal.body) > _MAX_LEARNING_BODY_BYTES:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="payload_too_large",
        )
    try:
        value = json.loads(internal.body, object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_request_body",
        ) from error


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _canonical_uuid(value: str, label: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as error:
        raise ValueError(label) from error
    if str(parsed) != value:
        raise ValueError(label)
    return value


def _learning_domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate in {"28000", "42501"}:
        return _learning_not_found()
    if sqlstate == "23505":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency_conflict",
        )
    if sqlstate == "40001":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="progress_version_conflict",
        )
    if sqlstate == "22023":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_domain_request",
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="learning_unavailable",
    )


def _learning_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="learning_item_unavailable",
    )
