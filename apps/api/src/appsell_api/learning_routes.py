from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .catalog_routes import _domain_error
from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1")


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
    try:
        async with auth.domain_session(internal.envelope) as session:
            library = await session.scalar(
                text("select app_private.read_buyer_library(:identity_user_id)"),
                {"identity_user_id": internal.envelope.identity.user_id},
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"items": library or []}


@router.get("/me/library/assets/{seller_organization_id}/{asset_id}")
async def authorize_asset(
    request: Request, seller_organization_id: UUID, asset_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.asset.read")
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select asset_id,seed_key,file_name,title,media_type,byte_size,sha256 "
                            "from app_private.authorize_learning_asset("
                            ":identity_user_id,:seller_organization_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "seller_organization_id": seller_organization_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "asset": {
            "id": str(row["asset_id"]),
            "seed_key": row["seed_key"],
            "file_name": row["file_name"],
            "title": row["title"],
            "media_type": row["media_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
        }
    }
