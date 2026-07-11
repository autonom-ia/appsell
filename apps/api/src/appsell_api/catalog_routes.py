from __future__ import annotations

import json
import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SAFE_INTEGER = (1 << 53) - 1


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


class ProductCourseOfferUpdateBody(ProductCourseDraftBody):
    course_id: str
    offer_id: str
    expected_product_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    expected_course_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    expected_offer_version: int = Field(ge=0, le=_MAX_SAFE_INTEGER)
    offer_title: str
    offer_summary: str

    @field_validator("course_id", "offer_id")
    @classmethod
    def validate_resource_id(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid resource id")

    @field_validator("offer_title")
    @classmethod
    def validate_offer_title(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 2 <= len(value.encode("utf-8")) <= 120
            or _contains_control(value)
        ):
            raise ValueError("invalid offer title")
        return value

    @field_validator("offer_summary")
    @classmethod
    def validate_offer_summary(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 500 or _contains_control(value):
            raise ValueError("invalid offer summary")
        return value


class CourseVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid course id")


class ProductPublicationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    offer_id: str
    course_versions: list[CourseVersionBody] = Field(min_length=1, max_length=100)
    expected_product_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    expected_offer_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key", "offer_id")
    @classmethod
    def validate_uuid(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid UUID")

    @field_validator("course_versions")
    @classmethod
    def validate_unique_courses(cls, value: list[CourseVersionBody]) -> list[CourseVersionBody]:
        if len({course.id for course in value}) != len(value):
            raise ValueError("duplicate course version")
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
                            "and id = :cursor and status <> 'archived'"
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
                            "and product.status <> 'archived' and course.status <> 'archived' "
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


@router.get("/me/sellers/{seller_id}/products/{product_id}")
async def get_product_draft(request: Request, seller_id: UUID, product_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.read")
    _require_seller(internal, seller_id)
    rows = await _read_product_draft(auth, internal, seller_id, product_id)
    return _product_detail_response(rows)


@router.get("/me/sellers/{seller_id}/products/{product_id}/preview")
async def preview_product_draft(
    request: Request, seller_id: UUID, product_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.preview")
    _require_seller(internal, seller_id)
    rows = await _read_product_draft(auth, internal, seller_id, product_id)
    row = rows[0]
    detail = _product_detail_response(rows)
    readiness = detail["product"]["readiness"]
    return {
        "preview": True,
        "state": "unpublished_draft",
        "seller_organization_id": str(seller_id),
        "versions": {
            "product": row["version"],
            "course": row["course_draft_version"],
            "offer": row["offer_version"] or 0,
        },
        "can_publish": readiness["can_publish"],
        "has_unpublished_changes": detail["product"]["has_unpublished_changes"],
        "readiness": readiness,
        "readiness_violations": readiness["items"],
        "snapshot": {
            "schema_version": 1,
            "seller_organization_id": str(seller_id),
            "product": {
                "id": str(row["id"]),
                "slug": row["slug"],
                "title": row["title"],
                "description": row["description"],
            },
            "offer": (
                {
                    "id": str(row["offer_id"]),
                    "title": row["offer_title"],
                    "summary": row["offer_summary"],
                    "billing_authority": "external_unconfigured",
                }
                if row["offer_id"] is not None
                else None
            ),
            "courses": [
                {
                    "id": str(course["course_id"]),
                    "title": course["course_title"],
                    "description": course["course_description"],
                    "position": course["course_position"],
                }
                for course in rows
            ],
        },
    }


@router.patch("/me/sellers/{seller_id}/products/{product_id}")
async def update_product_draft(
    request: Request, seller_id: UUID, product_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.update")
    _require_seller(internal, seller_id)
    body = _model_from_body(ProductCourseOfferUpdateBody, internal.body)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select offer_id, product_version, course_version, "
                            "offer_version, created "
                            "from app_private.update_product_course_offer_draft("
                            ":identity_user_id, :seller_id, :product_id, :slug, "
                            ":course_id, :offer_id, :title, :description, :course_title, "
                            ":offer_title, :offer_summary, :expected_product_version, "
                            ":expected_course_version, :expected_offer_version, "
                            ":idempotency_key, :body_sha256, :request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "slug": body.slug,
                            "course_id": UUID(body.course_id),
                            "offer_id": UUID(body.offer_id),
                            "title": body.title,
                            "description": body.description,
                            "course_title": body.course_title,
                            "offer_title": body.offer_title,
                            "offer_summary": body.offer_summary,
                            "expected_product_version": body.expected_product_version,
                            "expected_course_version": body.expected_course_version,
                            "expected_offer_version": body.expected_offer_version,
                            "idempotency_key": UUID(body.idempotency_key),
                            "body_sha256": envelope.body_sha256,
                            "request_id": envelope.request_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "product_id": str(product_id),
        "course_id": body.course_id,
        "offer_id": str(row["offer_id"]),
        "product_version": row["product_version"],
        "course_version": row["course_version"],
        "offer_version": row["offer_version"],
        "created": bool(row["created"]),
    }


@router.post("/me/sellers/{seller_id}/products/{product_id}/publications", response_model=None)
async def publish_product(request: Request, seller_id: UUID, product_id: UUID) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.publication.create")
    _require_seller(internal, seller_id)
    body = _model_from_body(ProductPublicationBody, internal.body)
    envelope = internal.envelope
    expected_courses = json.dumps(
        {course.id: course.version for course in body.course_versions}, sort_keys=True
    )
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select publication_id, publication_version, product_version, "
                            "source_sha256, created from app_private.publish_product("
                            ":identity_user_id, :seller_id, :product_id, :offer_id, "
                            ":expected_product_version, :expected_offer_version, "
                            "cast(:expected_course_versions as jsonb), :idempotency_key, "
                            ":body_sha256, :request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "offer_id": UUID(body.offer_id),
                            "expected_product_version": body.expected_product_version,
                            "expected_offer_version": body.expected_offer_version,
                            "expected_course_versions": expected_courses,
                            "idempotency_key": UUID(body.idempotency_key),
                            "body_sha256": envelope.body_sha256,
                            "request_id": envelope.request_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    created = bool(row["created"])
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content={
            "product_id": str(product_id),
            "publication_id": str(row["publication_id"]),
            "publication_version": row["publication_version"],
            "product_version": row["product_version"],
            "source_sha256": row["source_sha256"],
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


def _canonical_uuid(value: str, message: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(message) from error
    if str(parsed) != value:
        raise ValueError(message)
    return value


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


async def _read_product_draft(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    seller_id: UUID,
    product_id: UUID,
) -> list[Any]:
    try:
        async with auth.domain_session(internal.envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select product.seller_organization_id, product.id, "
                            "product.slug, product.title, "
                            "product.description, product.status, product.version, "
                            "product.created_at, product.updated_at, "
                            "product.published_snapshot_id, "
                            "course.id as course_id, course.title as course_title, "
                            "course.description as course_description, "
                            "course.status as course_status, "
                            "course.draft_version as course_draft_version, "
                            "linked.position as course_position, "
                            "offer.id as offer_id, offer.title as offer_title, "
                            "offer.summary as offer_summary, offer.status as offer_status, "
                            "offer.version as offer_version, "
                            "publication.version as publication_version, "
                            "publication.source_sha256 as publication_source_sha256, "
                            "publication.snapshot as publication_snapshot, "
                            "publication.published_at "
                            "from catalog_product product "
                            "join product_course linked "
                            "on linked.seller_organization_id = product.seller_organization_id "
                            "and linked.catalog_product_id = product.id "
                            "join course "
                            "on course.seller_organization_id = linked.seller_organization_id "
                            "and course.id = linked.course_id "
                            "left join offer "
                            "on offer.seller_organization_id = product.seller_organization_id "
                            "and offer.catalog_product_id = product.id "
                            "left join publication "
                            "on publication.seller_organization_id = "
                            "product.seller_organization_id "
                            "and publication.catalog_product_id = product.id "
                            "and publication.id = product.published_snapshot_id "
                            "where product.seller_organization_id = :seller_id "
                            "and product.id = :product_id "
                            "order by linked.position, course.id"
                        ),
                        {"seller_id": seller_id, "product_id": product_id},
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product_not_found")
    return list(rows)


def _product_detail_response(rows: list[Any]) -> dict[str, Any]:
    row = rows[0]
    violations: list[dict[str, Any]] = []
    if row["status"] == "archived":
        violations.append(_readiness_item("product_archived"))
    if any(course["course_status"] == "archived" for course in rows):
        violations.append(_readiness_item("course_archived"))
    if row["offer_id"] is None:
        violations.append(_readiness_item("offer_required"))
    elif row["offer_status"] != "draft":
        violations.append(_readiness_item("offer_not_publishable"))
    has_unpublished_changes = (
        row["published_snapshot_id"] is None or _draft_snapshot(rows) != row["publication_snapshot"]
    )
    if not has_unpublished_changes:
        violations.append(_readiness_item("no_unpublished_changes"))
    courses = [
        {
            "id": str(course["course_id"]),
            "title": course["course_title"],
            "description": course["course_description"],
            "status": course["course_status"],
            "draft_version": course["course_draft_version"],
            "position": course["course_position"],
        }
        for course in rows
    ]
    course = courses[0]
    offer = (
        {
            "id": str(row["offer_id"]),
            "title": row["offer_title"],
            "summary": row["offer_summary"],
            "status": row["offer_status"],
            "version": row["offer_version"],
            "draft_version": row["offer_version"],
        }
        if row["offer_id"] is not None
        else None
    )
    publication = (
        {
            "id": str(row["published_snapshot_id"]),
            "version": row["publication_version"],
            "source_sha256": row["publication_source_sha256"],
            "published_at": row["published_at"].isoformat(),
        }
        if row["published_snapshot_id"] is not None
        else None
    )
    return {
        "product": {
            "id": str(row["id"]),
            "seller_organization_id": str(row["seller_organization_id"]),
            "slug": row["slug"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "version": row["version"],
            "draft_version": row["version"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
            "course": course,
            "courses": courses,
            "offer": offer,
            "publication": publication,
            "has_unpublished_changes": has_unpublished_changes,
            "readiness": {
                "can_publish": not violations,
                "items": violations or [_readiness_item("server_validation_complete", ready=True)],
            },
        }
    }


def _draft_snapshot(rows: list[Any]) -> dict[str, Any]:
    row = rows[0]
    return {
        "schema_version": 1,
        "seller_organization_id": str(row["seller_organization_id"]),
        "product": {
            "id": str(row["id"]),
            "slug": row["slug"],
            "title": row["title"],
            "description": row["description"],
        },
        "offer": (
            {
                "id": str(row["offer_id"]),
                "title": row["offer_title"],
                "summary": row["offer_summary"],
                "billing_authority": "external_unconfigured",
            }
            if row["offer_id"] is not None
            else None
        ),
        "courses": [
            {
                "id": str(course["course_id"]),
                "title": course["course_title"],
                "description": course["course_description"],
                "position": course["course_position"],
            }
            for course in rows
        ],
    }


def _readiness_item(key: str, *, ready: bool = False) -> dict[str, Any]:
    labels = {
        "product_archived": "O produto está arquivado.",
        "course_archived": "Um curso vinculado está arquivado.",
        "offer_required": "Crie e salve a oferta informativa.",
        "offer_not_publishable": "A oferta não está em estado publicável.",
        "no_unpublished_changes": "Não há alterações novas para publicar.",
        "server_validation_complete": "Validação do servidor concluída.",
    }
    return {"key": key, "label": labels[key], "ready": ready}


def _domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == "23505":
        if getattr(error.orig, "constraint_name", None) == "catalog_product_seller_slug_key":
            return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slug_conflict")
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="resource_conflict")
    if sqlstate == "40001":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="draft_version_conflict")
    if sqlstate == "P4091":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no_unpublished_changes",
        )
    if sqlstate == "22023":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_domain_request",
        )
    if sqlstate in {"28000", "42501"}:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if sqlstate == "55000":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="publication_not_ready",
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="domain_unavailable",
    )
