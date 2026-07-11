from __future__ import annotations

import json
import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ProductCourseDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    slug: str
    title: str
    description: str
    course_title: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ValueError("invalid idempotency key") from error
        if str(parsed) != value:
            raise ValueError("invalid idempotency key")
        return value

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not 3 <= len(value.encode("utf-8")) <= 63 or _SLUG.fullmatch(value) is None:
            raise ValueError("invalid product slug")
        return value

    @field_validator("title", "course_title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 2 <= len(value.encode("utf-8")) <= 160
            or _contains_control(value)
        ):
            raise ValueError("invalid title")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 2000 or _contains_control(value):
            raise ValueError("invalid description")
        return value


@router.get("/me/sellers/{seller_id}/products")
async def list_product_drafts(
    request: Request,
    seller_id: UUID,
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.read")
    _require_seller(internal, seller_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "with cursor_row as ("
                            "select created_at, id from catalog_product "
                            "where seller_organization_id = :seller_id "
                            "and id = :cursor and status = 'draft'"
                            ") "
                            "select product.id, product.slug, product.title, "
                            "product.description, product.status, product.version, "
                            "product.created_at, product.updated_at, "
                            "course.id as course_id, course.title as course_title, "
                            "course.status as course_status, "
                            "course.draft_version as course_draft_version "
                            "from catalog_product product "
                            "join product_course linked "
                            "on linked.seller_organization_id = product.seller_organization_id "
                            "and linked.catalog_product_id = product.id "
                            "and linked.position = 0 "
                            "join course "
                            "on course.seller_organization_id = linked.seller_organization_id "
                            "and course.id = linked.course_id "
                            "where product.seller_organization_id = :seller_id "
                            "and product.status = 'draft' and course.status = 'draft' "
                            "and (:cursor is null or (product.created_at, product.id) < "
                            "(select created_at, id from cursor_row)) "
                            "order by product.created_at desc, product.id desc "
                            "limit :fetch_limit"
                        ),
                        {
                            "seller_id": seller_id,
                            "cursor": cursor,
                            "fetch_limit": limit + 1,
                        },
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error

    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    return {
        "products": [_product_response(row) for row in visible_rows],
        "next_cursor": str(visible_rows[-1]["id"]) if has_more else None,
    }


@router.post("/me/sellers/{seller_id}/products", response_model=None)
async def create_product_draft(request: Request, seller_id: UUID) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.draft.create")
    _require_seller(internal, seller_id)
    body = _model_from_body(ProductCourseDraftBody, internal.body)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select catalog_product_id, course_id, created "
                            "from app_private.create_product_course_draft("
                            ":identity_user_id, :seller_id, :slug, :title, :description, "
                            ":course_title, :idempotency_key, :body_sha256, :request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "slug": body.slug,
                            "title": body.title,
                            "description": body.description,
                            "course_title": body.course_title,
                            "idempotency_key": UUID(body.idempotency_key),
                            "body_sha256": envelope.body_sha256,
                            "request_id": envelope.request_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="product_draft_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error

    created = bool(row["created"])
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content={
            "product_id": str(row["catalog_product_id"]),
            "course_id": str(row["course_id"]),
            "status": "draft",
            "created": created,
        },
    )


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


def _require_seller(internal: AuthenticatedInternalRequest, seller_id: UUID) -> None:
    if internal.envelope.seller_organization_id != seller_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def _model_from_body(model: type[BaseModel], raw: bytes) -> Any:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
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


def _contains_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _product_response(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "slug": row["slug"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "version": row["version"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "course": {
            "id": str(row["course_id"]),
            "title": row["course_title"],
            "status": row["course_status"],
            "draft_version": row["course_draft_version"],
        },
    }


def _domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == "23505":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="resource_conflict")
    if sqlstate == "22023":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_domain_request",
        )
    if sqlstate in {"28000", "42501"}:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="domain_unavailable",
    )
