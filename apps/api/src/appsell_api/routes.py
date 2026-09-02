from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class IdentitySyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    email: str
    email_verified: bool
    # O nome é acessório e opcional: nem todo caminho de login emite o claim,
    # e a ausência dele não pode impedir a sincronização da identidade.
    display_name: str | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed or len(trimmed) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in trimmed):
            return None
        return trimmed

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if (
            value != value.strip().lower()
            or not 3 <= len(value.encode("utf-8")) <= 320
            or value.find("@") <= 0
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise ValueError("invalid normalized email")
        return value


# As mutações da conta não levam dado nenhum: o corpo é `{}` e qualquer campo
# a mais é recusado — inclusive um id de usuário que alguém tente enfiar.
class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SellerOnboardingBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    slug: str
    display_name: str

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
        if not 3 <= len(value) <= 63 or _SLUG.fullmatch(value) is None:
            raise ValueError("invalid seller slug")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 2 <= len(value) <= 120
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise ValueError("invalid seller display name")
        return value


@router.put("/me")
async def sync_me(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "identity.sync")
    await _require_no_seller(internal)
    body = await _model_from_body(IdentitySyncBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            identity_id = await session.scalar(
                text(
                    "select app_private.sync_identity("
                    ":identity_user_id,:issuer,:subject,:email,:verified,:display_name)"
                ),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "issuer": envelope.identity.issuer,
                    "subject": envelope.identity.sub,
                    "email": body.email,
                    "verified": body.email_verified,
                    "display_name": body.display_name,
                },
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if identity_id != envelope.identity.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return {
        "id": str(identity_id),
        "email": body.email,
        "email_verified": body.email_verified,
        "display_name": body.display_name,
    }


@router.get("/me")
async def read_me(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "platform.grants.read")
    await _require_no_seller(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            identity = (
                (
                    await session.execute(
                        text(
                            "select id, email_normalized, email_verified, display_name "
                            "from identity_user where id = :identity_user_id"
                        ),
                        {"identity_user_id": internal.envelope.identity.user_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            grants = (
                (
                    await session.execute(
                        text(
                            "select role from platform_grant "
                            "where identity_user_id = :identity_user_id and status = 'active' "
                            "order by role"
                        ),
                        {"identity_user_id": internal.envelope.identity.user_id},
                    )
                )
                .scalars()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="identity_not_found")
    return {
        "id": str(identity["id"]),
        "email": identity["email_normalized"],
        "email_verified": identity["email_verified"],
        "display_name": identity["display_name"],
        "platform_roles": list(grants),
    }


# Minha conta (#351). O sujeito NUNCA vem do corpo nem da query: é o do
# envelope assinado, e a função do banco ainda confere que o contexto é o
# mesmo. As três capabilities são exclusivas destas rotas.
@router.get("/me/account")
async def read_account(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "account.read")
    await _require_no_seller(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            requested_at = await session.scalar(
                text(
                    "select deletion_requested_at "
                    "from app_private.read_account_status(:identity_user_id)"
                ),
                {"identity_user_id": internal.envelope.identity.user_id},
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"deletion_requested_at": _iso(requested_at)}


@router.post("/me/account/export")
async def export_account(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "account.export")
    await _require_no_seller(internal)
    await _model_from_body(EmptyBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            exported = await session.scalar(
                text("select app_private.export_account_data(:identity_user_id,:request_id)"),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "request_id": envelope.request_id,
                },
            )
    except DBAPIError as error:
        # Acima do teto o banco recusa montar o documento (54000). Não é
        # indisponibilidade: é um caso para atendimento manual, e a tela diz isso.
        if getattr(error.orig, "sqlstate", None) == "54000":
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="account_export_too_large",
            ) from error
        raise _domain_error(error) from error
    if not isinstance(exported, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="domain_unavailable"
        )
    return exported


@router.post("/me/account/deletion-request")
async def request_account_deletion(request: Request) -> Response:
    auth, internal = await _authenticate(request, "account.deletion.request")
    await _require_no_seller(internal)
    await _model_from_body(EmptyBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select requested_at,already_requested "
                            "from app_private.request_account_deletion("
                            ":identity_user_id,:request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "request_id": envelope.request_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    already = bool(row["already_requested"])
    return Response(
        content=json.dumps(
            {"requested_at": _iso(row["requested_at"]), "already_requested": already}
        ),
        status_code=status.HTTP_200_OK if already else status.HTTP_201_CREATED,
        media_type="application/json",
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


@router.get("/me/sellers")
async def list_sellers(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "seller.list")
    await _require_no_seller(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select id,slug,display_name,role "
                            "from app_private.list_accessible_sellers(:identity_user_id)"
                        ),
                        {"identity_user_id": internal.envelope.identity.user_id},
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "sellers": [
            {
                "id": str(row["id"]),
                "slug": row["slug"],
                "display_name": row["display_name"],
                "role": row["role"],
            }
            for row in rows
        ]
    }


@router.post("/me/sellers", status_code=status.HTTP_201_CREATED)
async def onboard_seller(request: Request) -> dict[str, str]:
    auth, internal = await _authenticate(request, "seller.onboard")
    await _require_no_seller(internal)
    body = await _model_from_body(SellerOnboardingBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            seller_id = await session.scalar(
                text(
                    "select app_private.onboard_seller("
                    ":identity_user_id,:slug,:display_name,:idempotency_key,"
                    ":body_sha256,:request_id)"
                ),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "slug": body.slug,
                    "display_name": body.display_name,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": envelope.body_sha256,
                    "request_id": envelope.request_id,
                },
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="seller_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"id": str(seller_id)}


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


async def _require_no_seller(internal: AuthenticatedInternalRequest) -> None:
    if internal.envelope.seller_organization_id is not None:
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


def _domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate in {"23505"}:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="resource_conflict")
    if sqlstate in {"22023"}:
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
