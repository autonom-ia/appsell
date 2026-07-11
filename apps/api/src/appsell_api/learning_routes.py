from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .catalog_routes import _domain_error
from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator
from .storage_s3 import S3Storage, StorageError

router = APIRouter(prefix="/v1")
_MAX_LEARNING_BODY_BYTES = 4_096
_MAX_SAFE_INTEGER = (1 << 53) - 1


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
    auth, internal = await _authenticate(request, "library.read")
    _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            library = await session.scalar(
                text("select app_private.read_buyer_library(:identity_user_id)"),
                {"identity_user_id": internal.envelope.identity.user_id},
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"items": library or []}


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}")
async def authorize_asset(request: Request, entitlement_id: UUID, asset_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.asset.read")
    _require_buyer_scope(internal)
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
            "seed_key": str(row["file_name"]).removesuffix(".pdf"),
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
    _require_buyer_scope(internal)
    player = await _read_player(auth, internal, entitlement_id, None)
    return {"player": player}


@router.get("/me/learning/items/{entitlement_id}/lessons/{lesson_id}")
async def read_learning_lesson(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.player.read")
    _require_buyer_scope(internal)
    player = await _read_player(auth, internal, entitlement_id, lesson_id)
    return {"player": player}


@router.put("/me/learning/items/{entitlement_id}/lessons/{lesson_id}/progress")
async def update_learning_progress(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.progress.write")
    _require_buyer_scope(internal)
    body = _model_from_internal_body(request, internal, LessonProgressBody)
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
    _require_buyer_scope(internal)
    body = _model_from_internal_body(request, internal, LessonCompletionBody)
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
) -> Any:
    try:
        async with auth.domain_session(internal.envelope) as session:
            player = await session.scalar(
                text(
                    "select app_private.read_learning_player("
                    ":identity_user_id,:entitlement_id,:lesson_id)"
                ),
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


def _require_buyer_scope(internal: AuthenticatedInternalRequest) -> None:
    if internal.envelope.seller_organization_id is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_internal_request",
        )


def _model_from_internal_body(
    request: Request,
    internal: AuthenticatedInternalRequest,
    model: type[BaseModel],
) -> Any:
    if request.headers.get("content-type") != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="invalid_content_type",
        )
    if len(internal.body) > _MAX_LEARNING_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="payload_too_large",
        )
    try:
        value = json.loads(internal.body, object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
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
