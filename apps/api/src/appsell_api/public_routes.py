from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .internal_requests import InternalRequestAuthenticator

router = APIRouter(prefix="/v1/public")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@router.get("/sellers/{seller_slug}")
async def read_public_storefront(seller_slug: str, request: Request) -> dict[str, Any]:
    if not 3 <= len(seller_slug) <= 63 or _SLUG.fullmatch(seller_slug) is None:
        raise _not_found()
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if auth is None or session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="public_storefront_unavailable",
        )
    auth.require_service_credential(request)
    try:
        async with session_factory() as session, session.begin():
            projection = await session.scalar(
                text("select app_private.read_public_storefront(:seller_slug)"),
                {"seller_slug": seller_slug},
            )
    except DBAPIError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="public_storefront_unavailable",
        ) from error
    if not isinstance(projection, dict):
        raise _not_found()
    return projection


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="storefront_not_found")
