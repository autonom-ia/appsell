from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .capacity_telemetry import (
    capacity_product_create_trace,
    emit_capacity_product_create_trace,
)
from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN_PREFIX = re.compile(r"[0-9a-f]{8}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_SAFE_INTEGER = (1 << 53) - 1
# R$ 999,999,999.99 in minor units (centavos); mirrors the offer table CHECK.
_MAX_UNIT_AMOUNT_MINOR = 99_999_999_999
_BILLING_PERIODS = ("one_time", "monthly", "yearly")


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


class ProductCourseDraftUpdateBody(ProductCourseDraftBody):
    """Editorial product/course save; deliberately has no offer fields."""

    course_id: str
    expected_product_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    expected_course_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)

    @field_validator("course_id")
    @classmethod
    def validate_course_id(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid course id")


class ProductCourseOfferUpdateBody(ProductCourseDraftBody):
    course_id: str
    offer_id: str
    expected_product_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    expected_course_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    expected_offer_version: int = Field(ge=0, le=_MAX_SAFE_INTEGER)
    offer_title: str
    offer_summary: str
    # Billing is not connected yet: the seller editor intentionally does not collect
    # price ("Cobrança não conectada / não informe preço nesta fase") and migration
    # 0018 defers the offer's ``draft -> active`` transition, where price becomes
    # mandatory. Saving a draft therefore keeps price OPTIONAL: an absent field is sent
    # to the atomic function as NULL, which DEFAULTS on create (0 / BRL / one_time) and
    # PRESERVES the stored value on update -- so an editorial PATCH that omits price can
    # never silently wipe an already-priced offer. When a value IS sent it is still
    # fully validated below.
    offer_unit_amount_minor: int | None = Field(default=None, ge=0, le=_MAX_UNIT_AMOUNT_MINOR)
    offer_currency: str | None = None
    offer_billing_period: Literal["one_time", "monthly", "yearly"] | None = None

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

    @field_validator("offer_currency")
    @classmethod
    def validate_offer_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _CURRENCY.fullmatch(value) is None:
            raise ValueError("invalid offer currency")
        return value


class OfferStatusTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_status: Literal["draft", "active", "archived"]
    status: Literal["draft", "active", "archived"]
    expected_offer_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if (self.expected_status, self.status) not in {
            ("draft", "active"),
            ("active", "archived"),
            ("active", "draft"),
        }:
            raise ValueError("invalid offer status transition")
        return self


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
    # Publishing decides how the product is reached. "free" keeps the offer in
    # draft, which is the shape the free-access grant recognises; "paid" activates
    # the offer inside the same transaction so a paid catalog is never briefly
    # enrollable for free. Defaulting to "free" preserves the behaviour of every
    # caller written before this field existed.
    access_mode: Literal["free", "paid"] = "free"

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


class AccessLinkCreateBody(BaseModel):
    """A seat-limited invitation to a free product.

    The caller sends only the digest and the display prefix of the token it
    minted. The raw token stays in the browser that created it, so neither this
    service nor the database can hand it back, and a database dump grants
    nobody access.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    label: str = Field(min_length=1, max_length=80)
    token_sha256: str
    token_prefix: str
    max_uses: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: datetime | None = None

    @field_validator("idempotency_key")
    @classmethod
    def validate_uuid(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid UUID")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value != value.strip() or _CONTROL.search(value):
            raise ValueError("invalid label")
        return value

    @field_validator("token_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("invalid token digest")
        return value

    @field_validator("token_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not _TOKEN_PREFIX.fullmatch(value):
            raise ValueError("invalid token prefix")
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("expires_at requires a timezone")
        return value


class AccessLinkMutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_uuid(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid UUID")


class AccessLinkRotateBody(AccessLinkMutationBody):
    token_sha256: str
    token_prefix: str

    @field_validator("token_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("invalid token digest")
        return value

    @field_validator("token_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not _TOKEN_PREFIX.fullmatch(value):
            raise ValueError("invalid token prefix")
        return value


class CurriculumMutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_course_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


class LessonMaterialBody(CurriculumMutationBody):
    """An external link or an embedded video attached to a lesson.

    An embed is a provider and an identifier, never markup: refusing anything
    else here means the browser is never handed something to interpret. A link is
    accepted only over https and is never fetched by this service.
    """

    kind: Literal["external", "embed"]
    title: str
    url: str | None = None
    provider: Literal["youtube", "vimeo"] | None = None
    video_id: str | None = None

    @field_validator("title")
    @classmethod
    def validate_material_title(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 2 <= len(value.encode("utf-8")) <= 160
            or _contains_control(value)
        ):
            raise ValueError("invalid material title")
        return value

    @field_validator("url")
    @classmethod
    def validate_material_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value.startswith("https://")
            or not 11 < len(value) <= 2000
            or _contains_control(value)
            or any(character in value for character in ' <>"')
        ):
            raise ValueError("invalid material url")
        return value

    @field_validator("video_id")
    @classmethod
    def validate_material_video_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _VIDEO_ID.fullmatch(value) is None:
            raise ValueError("invalid material video id")
        return value

    @model_validator(mode="after")
    def validate_material_shape(self) -> Self:
        if self.kind == "external" and (
            self.url is None or self.provider is not None or self.video_id is not None
        ):
            raise ValueError("an external material carries only a url")
        if self.kind == "embed" and (
            self.url is not None or self.provider is None or self.video_id is None
        ):
            raise ValueError("an embedded material carries only a provider and an id")
        return self


class ModuleMutationBody(CurriculumMutationBody):
    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 2 <= len(value.encode("utf-8")) <= 160
            or _contains_control(value)
        ):
            raise ValueError("invalid module title")
        return value


class LessonMutationBody(ModuleMutationBody):
    kind: Literal["text"]
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 8_192 or _contains_control(
            value, allow_text_whitespace=True
        ):
            raise ValueError("invalid lesson content")
        return value


class ModuleOrderBody(CurriculumMutationBody):
    ordered_ids: list[str] = Field(max_length=50)

    @field_validator("ordered_ids")
    @classmethod
    def validate_order(cls, value: list[str]) -> list[str]:
        canonical = [_canonical_uuid(entry, "invalid module id") for entry in value]
        if len(set(canonical)) != len(canonical):
            raise ValueError("duplicate module id")
        return canonical


class LessonOrderBody(CurriculumMutationBody):
    ordered_ids: list[str] = Field(max_length=100)

    @field_validator("ordered_ids")
    @classmethod
    def validate_order(cls, value: list[str]) -> list[str]:
        canonical = [_canonical_uuid(entry, "invalid lesson id") for entry in value]
        if len(set(canonical)) != len(canonical):
            raise ValueError("duplicate lesson id")
        return canonical


@router.get("/me/sellers/{seller_id}/products")
async def list_product_drafts(
    request: Request,
    seller_id: UUID,
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.read")
    await _require_seller(internal, seller_id)
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
                            "course.draft_version as course_draft_version, "
                            "current_offer.status as offer_status, "
                            "current_offer.unit_amount_minor as offer_unit_amount_minor, "
                            "current_offer.currency as offer_currency "
                            "from catalog_product product "
                            "join product_course linked "
                            "on linked.seller_organization_id = product.seller_organization_id "
                            "and linked.catalog_product_id = product.id "
                            "and linked.position = 0 "
                            "join course "
                            "on course.seller_organization_id = linked.seller_organization_id "
                            "and course.id = linked.course_id "
                            # Uma oferta por produto na prática, mas a ordem é
                            # explícita para o preço nunca depender de sorte:
                            # a ativa manda, e entre rascunhos vence a mais nova.
                            "left join lateral ("
                            "select candidate.status, candidate.unit_amount_minor, "
                            "candidate.currency from offer candidate "
                            "where candidate.seller_organization_id = "
                            "product.seller_organization_id "
                            "and candidate.catalog_product_id = product.id "
                            "and candidate.status <> 'archived' "
                            "order by (candidate.status = 'active') desc, candidate.version desc "
                            "limit 1"
                            ") current_offer on true "
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
    route_started = time.perf_counter()
    settings = getattr(request.app.state, "settings", None)
    trace = capacity_product_create_trace(
        request.headers.get("x-appsell-capacity-trace"),
        enabled=bool(getattr(settings, "capacity_product_create_telemetry", False)),
    )
    durations = {
        "internal_auth_verification": 0.0,
        "pool_checkout_replay_apply_context": 0.0,
        "sql": 0.0,
        "commit_response": 0.0,
    }
    trace_status = 500
    trace_outcome: Literal["success", "http_error", "exception"] = "exception"
    pre_domain_completed = False
    session_started: float | None = None
    sql_started: float | None = None
    sql_finished: float | None = None
    try:
        auth, internal = await _authenticate(request, "catalog.draft.create")
        await _require_seller(internal, seller_id)
        body = await _model_from_body(ProductCourseDraftBody, internal)
        envelope = internal.envelope
        session_started = time.perf_counter()
        durations["internal_auth_verification"] = (session_started - route_started) * 1_000
        pre_domain_completed = True
        async with auth.domain_session(envelope) as session:
            sql_started = time.perf_counter()
            durations["pool_checkout_replay_apply_context"] = (
                sql_started - session_started
            ) * 1_000
            try:
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
            finally:
                sql_finished = time.perf_counter()
                durations["sql"] = (sql_finished - sql_started) * 1_000
        created = bool(row["created"])
        response = JSONResponse(
            status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            content={
                "product_id": str(row["catalog_product_id"]),
                "course_id": str(row["course_id"]),
                "status": "draft",
                "created": created,
            },
        )
        trace_status = response.status_code
        trace_outcome = "success"
        return response
    except IntegrityError as error:
        trace_status = status.HTTP_409_CONFLICT
        trace_outcome = "http_error"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="product_draft_conflict"
        ) from error
    except DBAPIError as error:
        mapped = _domain_error(error)
        trace_status = mapped.status_code
        trace_outcome = "http_error"
        raise mapped from error
    except HTTPException as error:
        trace_status = error.status_code
        trace_outcome = "http_error"
        raise
    except Exception:
        raise
    finally:
        concluded_at = time.perf_counter()
        if not pre_domain_completed:
            durations["internal_auth_verification"] = (concluded_at - route_started) * 1_000
        elif sql_started is None and session_started is not None:
            durations["pool_checkout_replay_apply_context"] = (
                concluded_at - session_started
            ) * 1_000
        elif sql_finished is not None:
            durations["commit_response"] = (concluded_at - sql_finished) * 1_000
        if trace is not None:
            emit_capacity_product_create_trace(
                trace=trace,
                status_code=trace_status,
                outcome=trace_outcome,
                total_ms=(concluded_at - route_started) * 1_000,
                durations_ms=durations,
            )


@router.get("/me/sellers/{seller_id}/products/{product_id}")
async def get_product_draft(request: Request, seller_id: UUID, product_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.read")
    await _require_seller(internal, seller_id)
    rows = await _read_product_draft(auth, internal, seller_id, product_id, "catalog.draft.read")
    return _product_detail_response(rows)


@router.get("/me/sellers/{seller_id}/products/{product_id}/preview")
async def preview_product_draft(
    request: Request, seller_id: UUID, product_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.preview")
    await _require_seller(internal, seller_id)
    rows = await _read_product_draft(auth, internal, seller_id, product_id, "catalog.draft.preview")
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
        "snapshot": _draft_snapshot(rows),
    }


@router.get("/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/curriculum")
async def read_course_curriculum(
    request: Request, seller_id: UUID, product_id: UUID, course_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.read")
    await _require_seller(internal, seller_id)
    row = await _execute_curriculum(
        auth,
        internal,
        "select course_version, curriculum, module_total, lesson_total, "
        "lesson_totals_by_module from app_private.read_course_curriculum("
        ":identity_user_id, :seller_id, :product_id, :course_id)",
        {
            "identity_user_id": internal.envelope.identity.user_id,
            "seller_id": seller_id,
            "product_id": product_id,
            "course_id": course_id,
        },
    )
    return {
        "course": {"id": str(course_id), "draft_version": row["course_version"]},
        "modules": row["curriculum"],
        "limits": {
            "modules_per_course": 50,
            "lessons_per_module": 100,
            "lessons_per_course": 500,
            "title_bytes": 160,
            "lesson_content_bytes": 8_192,
        },
        "usage": {
            "modules_total": row["module_total"],
            "lessons_total": row["lesson_total"],
            "lessons_by_module": row["lesson_totals_by_module"],
        },
    }


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules",
    response_model=None,
)
async def create_course_module(
    request: Request, seller_id: UUID, product_id: UUID, course_id: UUID
) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.curriculum.create")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(ModuleMutationBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select module_id, course_version, created from app_private.create_course_module("
        ":identity_user_id, :seller_id, :product_id, :course_id, :title, "
        ":expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {"title": body.title},
    )
    created = bool(row["created"])
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content={
            "module_id": str(row["module_id"]),
            "course_version": row["course_version"],
            "created": created,
        },
    )


@router.patch(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules/{module_id}"
)
async def update_course_module(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(ModuleMutationBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select course_version, updated from app_private.update_course_module("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, :title, "
        ":expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {"module_id": module_id, "title": body.title},
    )
    return {
        "module_id": str(module_id),
        "course_version": row["course_version"],
        "updated": bool(row["updated"]),
    }


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules/{module_id}/archive"
)
async def archive_course_module(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.archive")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(CurriculumMutationBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select course_version, archived from app_private.archive_course_module("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, "
        ":expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {"module_id": module_id},
    )
    return {
        "module_id": str(module_id),
        "course_version": row["course_version"],
        "archived": bool(row["archived"]),
    }


@router.put("/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules/order")
async def reorder_course_modules(
    request: Request, seller_id: UUID, product_id: UUID, course_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.reorder")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(ModuleOrderBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select course_version, reordered from app_private.reorder_course_modules("
        ":identity_user_id, :seller_id, :product_id, :course_id, :ordered_ids, "
        ":expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {"ordered_ids": [UUID(entry) for entry in body.ordered_ids]},
    )
    return {"course_version": row["course_version"], "reordered": bool(row["reordered"])}


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules/{module_id}/lessons",
    response_model=None,
)
async def create_course_lesson(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.curriculum.create")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(LessonMutationBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select lesson_id, course_version, created from app_private.create_course_lesson("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, :title, "
        ":content, :expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {"module_id": module_id, "title": body.title, "content": body.content},
    )
    created = bool(row["created"])
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content={
            "lesson_id": str(row["lesson_id"]),
            "course_version": row["course_version"],
            "created": created,
        },
    )


@router.patch(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules/{module_id}/lessons/{lesson_id}"
)
async def update_course_lesson(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(LessonMutationBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select course_version, updated from app_private.update_course_lesson("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, :lesson_id, "
        ":title, :content, :expected_course_version, :idempotency_key, :body_sha256, "
        ":request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {
            "module_id": module_id,
            "lesson_id": lesson_id,
            "title": body.title,
            "content": body.content,
        },
    )
    return {
        "lesson_id": str(lesson_id),
        "course_version": row["course_version"],
        "updated": bool(row["updated"]),
    }


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules/{module_id}/lessons/{lesson_id}/archive"
)
async def archive_course_lesson(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.archive")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(CurriculumMutationBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select course_version, archived from app_private.archive_course_lesson("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, :lesson_id, "
        ":expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {"module_id": module_id, "lesson_id": lesson_id},
    )
    return {
        "lesson_id": str(lesson_id),
        "course_version": row["course_version"],
        "archived": bool(row["archived"]),
    }


@router.put(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}/modules/{module_id}/lessons/order"
)
async def reorder_course_lessons(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.reorder")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(LessonOrderBody, internal)
    row = await _execute_curriculum(
        auth,
        internal,
        "select course_version, reordered from app_private.reorder_module_lessons("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, :ordered_ids, "
        ":expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        _curriculum_parameters(internal, seller_id, product_id, course_id, body)
        | {
            "module_id": module_id,
            "ordered_ids": [UUID(entry) for entry in body.ordered_ids],
        },
    )
    return {"course_version": row["course_version"], "reordered": bool(row["reordered"])}


@router.patch("/me/sellers/{seller_id}/products/{product_id}/metadata")
async def update_product_course_draft(
    request: Request, seller_id: UUID, product_id: UUID
) -> dict[str, Any]:
    """Save the editorial step without requiring a commercial offer."""

    auth, internal = await _authenticate(request, "catalog.draft.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(ProductCourseDraftUpdateBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select product_version, course_version, updated "
                            "from app_private.update_product_course_draft("
                            ":identity_user_id, :seller_id, :product_id, :slug, "
                            ":course_id, :title, :description, :course_title, "
                            ":expected_product_version, :expected_course_version, "
                            ":idempotency_key, :body_sha256, :request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "slug": body.slug,
                            "course_id": UUID(body.course_id),
                            "title": body.title,
                            "description": body.description,
                            "course_title": body.course_title,
                            "expected_product_version": body.expected_product_version,
                            "expected_course_version": body.expected_course_version,
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
        "product_version": row["product_version"],
        "course_version": row["course_version"],
        "updated": bool(row["updated"]),
    }


@router.patch("/me/sellers/{seller_id}/products/{product_id}")
async def update_product_draft(
    request: Request, seller_id: UUID, product_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(ProductCourseOfferUpdateBody, internal)
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
                            ":offer_title, :offer_summary, :offer_unit_amount_minor, "
                            ":offer_currency, :offer_billing_period, "
                            ":expected_product_version, "
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
                            "offer_unit_amount_minor": body.offer_unit_amount_minor,
                            "offer_currency": body.offer_currency,
                            "offer_billing_period": body.offer_billing_period,
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
        "offer_unit_amount_minor": body.offer_unit_amount_minor,
        "offer_currency": body.offer_currency,
        "offer_billing_period": body.offer_billing_period,
        "created": bool(row["created"]),
    }


@router.patch("/me/sellers/{seller_id}/products/{product_id}/offers/{offer_id}/status")
async def transition_offer_status(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    offer_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.offer.status.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(OfferStatusTransitionBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select offer_status, offer_version, changed "
                            "from app_private.transition_offer_status("
                            ":identity_user_id, :seller_id, :product_id, :offer_id, "
                            ":expected_offer_status, :offer_status, "
                            ":expected_offer_version, :idempotency_key, :body_sha256, "
                            ":request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "offer_id": offer_id,
                            "expected_offer_status": body.expected_status,
                            "offer_status": body.status,
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
        "offer_id": str(offer_id),
        "status": row["offer_status"],
        "offer_version": row["offer_version"],
        "changed": bool(row["changed"]),
    }


@router.post("/me/sellers/{seller_id}/products/{product_id}/publications", response_model=None)
async def publish_product(request: Request, seller_id: UUID, product_id: UUID) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.publication.create")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(ProductPublicationBody, internal)
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
                            "source_sha256, offer_status, offer_version, created "
                            "from app_private.publish_product_with_access("
                            ":identity_user_id, :seller_id, :product_id, :offer_id, "
                            ":expected_product_version, :expected_offer_version, "
                            "cast(:expected_course_versions as jsonb), :access_mode, "
                            ":idempotency_key, :body_sha256, :request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "offer_id": UUID(body.offer_id),
                            "expected_product_version": body.expected_product_version,
                            "expected_offer_version": body.expected_offer_version,
                            "expected_course_versions": expected_courses,
                            "access_mode": body.access_mode,
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
            "access_mode": body.access_mode,
            "offer_status": row["offer_status"],
            "offer_version": row["offer_version"],
            "created": created,
        },
    )


@router.post("/me/sellers/{seller_id}/products/{product_id}/access-links", response_model=None)
async def create_access_link(request: Request, seller_id: UUID, product_id: UUID) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.access_link.create")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AccessLinkCreateBody, internal)
    envelope = internal.envelope
    row = await _execute_access_link(
        auth,
        internal,
        "select access_link_id, token_prefix, label, max_uses, redeemed_count, "
        "expires_at, created_at from app_private.create_product_access_link("
        ":identity_user_id, :seller_id, :product_id, :label, :token_sha256, "
        ":token_prefix, :max_uses, :expires_at, :idempotency_key, :body_sha256, "
        ":request_id)",
        {
            "identity_user_id": envelope.identity.user_id,
            "seller_id": seller_id,
            "product_id": product_id,
            "label": body.label,
            "token_sha256": body.token_sha256,
            "token_prefix": body.token_prefix,
            "max_uses": body.max_uses,
            "expires_at": body.expires_at,
            "idempotency_key": UUID(body.idempotency_key),
            "body_sha256": envelope.body_sha256,
            "request_id": envelope.request_id,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=_access_link_payload(row),
    )


@router.get("/me/sellers/{seller_id}/products/{product_id}/access-links")
async def list_access_links(request: Request, seller_id: UUID, product_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.access_link.read")
    await _require_seller(internal, seller_id)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select access_link_id, token_prefix, label, max_uses, "
                            "redeemed_count, expires_at, revoked_at, rotated_at, created_at "
                            "from app_private.list_product_access_links("
                            ":identity_user_id, :seller_id, :product_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"access_links": [_access_link_payload(row) for row in rows]}


@router.get(
    "/me/sellers/{seller_id}/products/{product_id}/access-links/{access_link_id}/redemptions"
)
async def list_access_link_redemptions(
    request: Request, seller_id: UUID, product_id: UUID, access_link_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.access_link.read")
    await _require_seller(internal, seller_id)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select buyer_identity_user_id, entitlement_id, redeemed_at "
                            "from app_private.list_product_access_link_redemptions("
                            ":identity_user_id, :seller_id, :product_id, :access_link_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "access_link_id": access_link_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "redemptions": [
            {
                "buyer_identity_user_id": str(row["buyer_identity_user_id"]),
                "entitlement_id": str(row["entitlement_id"]),
                "redeemed_at": _isoformat(row["redeemed_at"]),
            }
            for row in rows
        ]
    }


@router.post("/me/sellers/{seller_id}/products/{product_id}/access-links/{access_link_id}/revoke")
async def revoke_access_link(
    request: Request, seller_id: UUID, product_id: UUID, access_link_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.access_link.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AccessLinkMutationBody, internal)
    envelope = internal.envelope
    row = await _execute_access_link(
        auth,
        internal,
        "select access_link_id, revoked_at, changed "
        "from app_private.revoke_product_access_link("
        ":identity_user_id, :seller_id, :product_id, :access_link_id, "
        ":idempotency_key, :body_sha256, :request_id)",
        {
            "identity_user_id": envelope.identity.user_id,
            "seller_id": seller_id,
            "product_id": product_id,
            "access_link_id": access_link_id,
            "idempotency_key": UUID(body.idempotency_key),
            "body_sha256": envelope.body_sha256,
            "request_id": envelope.request_id,
        },
    )
    return {
        "access_link_id": str(row["access_link_id"]),
        "revoked_at": _isoformat(row["revoked_at"]),
        "changed": bool(row["changed"]),
    }


@router.post("/me/sellers/{seller_id}/products/{product_id}/access-links/{access_link_id}/rotate")
async def rotate_access_link(
    request: Request, seller_id: UUID, product_id: UUID, access_link_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.access_link.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(AccessLinkRotateBody, internal)
    envelope = internal.envelope
    row = await _execute_access_link(
        auth,
        internal,
        "select access_link_id, token_prefix, redeemed_count, max_uses, rotated_at "
        "from app_private.rotate_product_access_link("
        ":identity_user_id, :seller_id, :product_id, :access_link_id, "
        ":token_sha256, :token_prefix, :idempotency_key, :body_sha256, :request_id)",
        {
            "identity_user_id": envelope.identity.user_id,
            "seller_id": seller_id,
            "product_id": product_id,
            "access_link_id": access_link_id,
            "token_sha256": body.token_sha256,
            "token_prefix": body.token_prefix,
            "idempotency_key": UUID(body.idempotency_key),
            "body_sha256": envelope.body_sha256,
            "request_id": envelope.request_id,
        },
    )
    return {
        "access_link_id": str(row["access_link_id"]),
        "token_prefix": row["token_prefix"],
        "redeemed_count": row["redeemed_count"],
        "max_uses": row["max_uses"],
        "rotated_at": _isoformat(row["rotated_at"]),
    }


async def _execute_access_link(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    statement: str,
    parameters: dict[str, Any],
) -> Any:
    try:
        async with auth.domain_session(internal.envelope) as session:
            return (await session.execute(text(statement), parameters)).mappings().one()
    except DBAPIError as error:
        raise _domain_error(error) from error


def _access_link_payload(row: Any) -> dict[str, Any]:
    # The digest never leaves the database and the raw token never reaches it,
    # so a link can only ever be reported by its display prefix.
    payload: dict[str, Any] = {
        "access_link_id": str(row["access_link_id"]),
        "token_prefix": row["token_prefix"],
        "label": row["label"],
        "max_uses": row["max_uses"],
        "redeemed_count": row["redeemed_count"],
        "expires_at": _isoformat(row["expires_at"]),
        "created_at": _isoformat(row["created_at"]),
    }
    if "revoked_at" in row:
        payload["revoked_at"] = _isoformat(row["revoked_at"])
    if "rotated_at" in row:
        payload["rotated_at"] = _isoformat(row["rotated_at"])
    return payload


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/materials"
)
async def create_lesson_material(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.curriculum.create")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(LessonMaterialBody, internal)
    envelope = internal.envelope
    row = await _execute_curriculum(
        auth,
        internal,
        "select material_id, course_version, position "
        "from app_private.create_lesson_material_link("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, :lesson_id, "
        ":kind, :title, :url, :provider, :video_id, :expected_course_version, "
        ":idempotency_key, :body_sha256, :request_id)",
        {
            "identity_user_id": envelope.identity.user_id,
            "seller_id": seller_id,
            "product_id": product_id,
            "course_id": course_id,
            "module_id": module_id,
            "lesson_id": lesson_id,
            "kind": body.kind,
            "title": body.title,
            "url": body.url,
            "provider": body.provider,
            "video_id": body.video_id,
            "expected_course_version": body.expected_course_version,
            "idempotency_key": UUID(body.idempotency_key),
            "body_sha256": envelope.body_sha256,
            "request_id": envelope.request_id,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "material_id": str(row["material_id"]),
            "course_version": row["course_version"],
            "position": row["position"],
        },
    )


@router.get(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/materials"
)
async def list_lesson_materials(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.read")
    await _require_seller(internal, seller_id)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select material_id, kind, title, url, provider, video_id, position "
                            "from app_private.list_lesson_material_links("
                            ":identity_user_id, :seller_id, :product_id, :course_id, "
                            ":module_id, :lesson_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "course_id": course_id,
                            "module_id": module_id,
                            "lesson_id": lesson_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "materials": [
            {
                "material_id": str(row["material_id"]),
                "kind": row["kind"],
                "title": row["title"],
                "url": row["url"],
                "provider": row["provider"],
                "video_id": row["video_id"],
                "position": row["position"],
            }
            for row in rows
        ]
    }


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/courses/{course_id}"
    "/modules/{module_id}/lessons/{lesson_id}/materials/{material_id}/archive"
)
async def archive_lesson_material(
    request: Request,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    module_id: UUID,
    lesson_id: UUID,
    material_id: UUID,
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.curriculum.archive")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(CurriculumMutationBody, internal)
    envelope = internal.envelope
    row = await _execute_curriculum(
        auth,
        internal,
        "select material_id, course_version, changed "
        "from app_private.archive_lesson_material_link("
        ":identity_user_id, :seller_id, :product_id, :course_id, :module_id, :lesson_id, "
        ":material_id, :expected_course_version, :idempotency_key, :body_sha256, :request_id)",
        {
            "identity_user_id": envelope.identity.user_id,
            "seller_id": seller_id,
            "product_id": product_id,
            "course_id": course_id,
            "module_id": module_id,
            "lesson_id": lesson_id,
            "material_id": material_id,
            "expected_course_version": body.expected_course_version,
            "idempotency_key": UUID(body.idempotency_key),
            "body_sha256": envelope.body_sha256,
            "request_id": envelope.request_id,
        },
    )
    return {
        "material_id": str(row["material_id"]),
        "course_version": row["course_version"],
        "changed": bool(row["changed"]),
    }


async def _execute_curriculum(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    statement: str,
    parameters: dict[str, Any],
) -> Any:
    try:
        async with auth.domain_session(internal.envelope) as session:
            return (await session.execute(text(statement), parameters)).mappings().one()
    except DBAPIError as error:
        raise _domain_error(error) from error


def _curriculum_parameters(
    internal: AuthenticatedInternalRequest,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    body: CurriculumMutationBody,
) -> dict[str, Any]:
    envelope = internal.envelope
    return {
        "identity_user_id": envelope.identity.user_id,
        "seller_id": seller_id,
        "product_id": product_id,
        "course_id": course_id,
        "expected_course_version": body.expected_course_version,
        "idempotency_key": UUID(body.idempotency_key),
        "body_sha256": envelope.body_sha256,
        "request_id": envelope.request_id,
    }


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


async def _require_seller(internal: AuthenticatedInternalRequest, seller_id: UUID) -> None:
    if internal.envelope.seller_organization_id != seller_id:
        await internal.consume_rejected_request()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def _model_from_body(model: type[BaseModel], internal: AuthenticatedInternalRequest) -> Any:
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


def _contains_control(value: str, *, allow_text_whitespace: bool = False) -> bool:
    allowed = {"\n", "\r", "\t"} if allow_text_whitespace else set()
    return any(
        character not in allowed and (ord(character) < 0x20 or ord(character) == 0x7F)
        for character in value
    )


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
        **_product_access_response(row),
    }


def _product_access_response(row: Any) -> dict[str, Any]:
    """Modo de acesso e preço de um produto na listagem.

    O preço só existe quando a oferta está ativa. Uma oferta em rascunho pode
    ter valor gravado, mas ninguém paga por ela ainda: devolvê-lo faria a lista
    anunciar um preço que não está em vigor.
    """
    if "offer_status" not in row.keys():
        return {}
    active = row["offer_status"] == "active"
    return {
        "access_mode": "paid" if active else "free",
        "offer_status": row["offer_status"],
        "unit_amount_minor": row["offer_unit_amount_minor"] if active else None,
        "currency": row["offer_currency"] if active else None,
    }


async def _read_product_draft(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    seller_id: UUID,
    product_id: UUID,
    capability: Literal["catalog.draft.read", "catalog.draft.preview"],
) -> list[Any]:
    try:
        async with auth.domain_session(internal.envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select product.seller_organization_id, "
                            "organization.slug as seller_slug, product.id, "
                            "product.slug, product.title, "
                            "product.description, product.status, product.version, "
                            "product.created_at, product.updated_at, "
                            "product.published_snapshot_id, "
                            "course.id as course_id, course.title as course_title, "
                            "course.description as course_description, "
                            "course.status as course_status, "
                            "course.draft_version as course_draft_version, "
                            "linked.position as course_position, "
                            "coalesce((select jsonb_agg(jsonb_build_object("
                            "'id', module.id, 'title', module.title, "
                            "'status', module.status, 'position', module.position, "
                            "'lessons', coalesce((select jsonb_agg(jsonb_build_object("
                            "'id', lesson.id, 'title', lesson.title, 'kind', 'text', "
                            "'content', lesson.body_text, 'status', lesson.status, "
                            "'position', lesson.position, 'assets', "
                            "app_private.list_product_draft_lesson_assets("
                            ":identity_user_id, :seller_id, :product_id, course.id, "
                            "module.id, lesson.id, :capability)) "
                            "order by lesson.position, lesson.id) "
                            "from lesson where lesson.seller_organization_id = "
                            "module.seller_organization_id and lesson.course_id = module.course_id "
                            "and lesson.module_id = module.id "
                            "and lesson.status = 'active'), '[]'::jsonb)) "
                            "order by module.position, module.id) from course_module module "
                            "where module.seller_organization_id = course.seller_organization_id "
                            "and module.course_id = course.id and module.status = 'active'), "
                            "'[]'::jsonb) as curriculum_modules, "
                            "offer.id as offer_id, offer.title as offer_title, "
                            "offer.summary as offer_summary, offer.status as offer_status, "
                            "offer.version as offer_version, "
                            "offer.unit_amount_minor as offer_unit_amount_minor, "
                            "rtrim(offer.currency) as offer_currency, "
                            "offer.billing_period as offer_billing_period, "
                            "publication.version as publication_version, "
                            "publication.source_sha256 as publication_source_sha256, "
                            "publication.snapshot as publication_snapshot, "
                            "publication.published_at, "
                            "app_private.product_draft_has_pending_asset_upload("
                            ":identity_user_id, :seller_id, :product_id, :capability) "
                            "as has_pending_asset_upload "
                            "from catalog_product product "
                            "join seller_organization organization "
                            "on organization.id = product.seller_organization_id "
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
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "seller_id": seller_id,
                            "product_id": product_id,
                            "capability": capability,
                        },
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
    if row.get("has_pending_asset_upload", False):
        violations.append(_readiness_item("asset_upload_pending"))
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
    curriculum_modules = 0
    curriculum_lessons = 0
    for course_row in rows:
        modules = course_row.get("curriculum_modules") or []
        if not isinstance(modules, list):
            continue
        curriculum_modules += len(modules)
        curriculum_lessons += sum(
            len(module.get("lessons") or []) for module in modules if isinstance(module, dict)
        )
    offer = (
        {
            "id": str(row["offer_id"]),
            "title": row["offer_title"],
            "summary": row["offer_summary"],
            "status": row["offer_status"],
            "version": row["offer_version"],
            "draft_version": row["offer_version"],
            "unit_amount_minor": row["offer_unit_amount_minor"],
            "currency": row["offer_currency"],
            "billing_period": row["offer_billing_period"],
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
            "seller_slug": row.get("seller_slug"),
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
            "curriculum": {
                "modules": curriculum_modules,
                "lessons": curriculum_lessons,
            },
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
    base = {
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
    return {
        **base,
        "schema_version": 4,
        "courses": [
            {
                "id": str(course["course_id"]),
                "title": course["course_title"],
                "description": course["course_description"],
                "position": course["course_position"],
                "modules": course.get("curriculum_modules", []),
            }
            for course in rows
        ],
    }


def _readiness_item(key: str, *, ready: bool = False) -> dict[str, Any]:
    labels = {
        "product_archived": "O produto está arquivado.",
        "course_archived": "Um curso vinculado está arquivado.",
        "offer_required": "Crie e salve a oferta informativa.",
        "offer_not_publishable": (
            "Desative vendas: a oferta precisa estar em rascunho para publicar o catálogo."
        ),
        "asset_upload_pending": (
            "Aguarde a verificação dos materiais antes de publicar o catálogo."
        ),
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
    if sqlstate == "P4092":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="offer_status_conflict",
        )
    if sqlstate == "P4221":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="snapshot_too_large",
        )
    if sqlstate == "P4222":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_offer_status_transition",
        )
    if sqlstate == "P4223":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="payout_account_required",
        )
    if sqlstate == "P4227":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="price_required_for_paid_access",
        )
    if sqlstate == "P4226":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="access_link_requires_free_product",
        )
    if sqlstate == "P4224":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="access_link_exhausted",
        )
    # A revoked, expired or unknown link answers identically, so probing the
    # endpoint cannot tell an attacker whether a token exists.
    if sqlstate == "P4225":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="access_link_unavailable",
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
