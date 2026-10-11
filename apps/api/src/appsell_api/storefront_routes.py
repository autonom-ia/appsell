from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .internal_requests import InternalRequestAuthenticator

router = APIRouter(prefix="/v1/public/storefronts")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@router.get("/{seller_slug}")
async def read_storefront(
    request: Request,
    seller_slug: str,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=24)] = 12,
) -> JSONResponse:
    _require_slug(seller_slug)
    if cursor is not None:
        _require_slug(cursor)
    auth, internal = await _authenticate(request)
    _require_public_scope(internal.envelope.seller_organization_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            payload = (
                await session.execute(
                    text("select app_private.read_public_storefront(:seller_slug,:cursor,:limit)"),
                    {"seller_slug": seller_slug, "cursor": cursor, "limit": limit},
                )
            ).scalar_one_or_none()
    except DBAPIError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="storefront_unavailable",
        ) from error
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="storefront_not_found")
    return JSONResponse(content=payload, headers={"cache-control": "no-store, max-age=0"})


@router.get("/{seller_slug}/products/{product_slug}")
async def read_public_product(
    request: Request,
    seller_slug: str,
    product_slug: str,
) -> JSONResponse:
    _require_slug(seller_slug)
    _require_slug(product_slug)
    auth, internal = await _authenticate(request)
    _require_public_scope(internal.envelope.seller_organization_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            payload = (
                await session.execute(
                    text("select app_private.read_public_product(:seller_slug,:product_slug)"),
                    {"seller_slug": seller_slug, "product_slug": product_slug},
                )
            ).scalar_one_or_none()
    except DBAPIError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="storefront_unavailable",
        ) from error
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product_not_found")
    return JSONResponse(content=payload, headers={"cache-control": "no-store, max-age=0"})


async def _authenticate(request: Request) -> tuple[InternalRequestAuthenticator, Any]:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_auth_not_configured",
        )
    return auth, await auth.authenticate(request, "storefront.public.read")


def _require_public_scope(seller_organization_id: Any) -> None:
    if seller_organization_id is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def _require_slug(value: str) -> None:
    if not 3 <= len(value.encode("utf-8")) <= 63 or _SLUG.fullmatch(value) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_public_slug",
        )
