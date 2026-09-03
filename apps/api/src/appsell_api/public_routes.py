from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .internal_requests import InternalRequestAuthenticator
from .storage_s3 import S3Storage, StorageError

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
    return public_images(projection, getattr(request.app.state, "storage", None))


def public_images(projection: dict[str, Any], storage: S3Storage | None) -> dict[str, Any]:
    """Troca chave e versão de objeto por um endereço temporário.

    O banco devolve logo, banner e capa como o que ele sabe: chave e versão no
    bucket servido. Nada disso pode chegar ao BFF, muito menos ao navegador —
    a chave denuncia o layout do armazenamento e a versão permite pedir um
    objeto que já saiu do ar. Aqui elas morrem: o que sai é `url` presignada
    (60 s, inline), tipo e dimensões. Sem armazenamento configurado, ou com
    a assinatura falhando, a chave some inteira: a tela usa as iniciais e a
    arte gerada, como faz para uma loja sem logo. Nunca uma URL quebrada.
    """
    seller = projection.get("seller")
    if isinstance(seller, dict) and ("logo" in seller or "banner" in seller):
        rewritten_seller = {
            key: value for key, value in seller.items() if key not in ("logo", "banner")
        }
        for field in ("logo", "banner"):
            if field in seller:
                signed = _signed_image(seller.get(field), storage)
                if signed is not None:
                    rewritten_seller[field] = signed
        projection = {**projection, "seller": rewritten_seller}
    products = projection.get("products")
    if isinstance(products, list):
        rewritten = []
        for product in products:
            if isinstance(product, dict) and ("cover" in product or "banner" in product):
                assinadas = {
                    campo: _signed_image(product.get(campo), storage)
                    for campo in ("cover", "banner")
                    if campo in product
                }
                product = {
                    key: value for key, value in product.items() if key not in ("cover", "banner")
                }
                for campo, assinada in assinadas.items():
                    if assinada is not None:
                        product[campo] = assinada
            rewritten.append(product)
        projection = {**projection, "products": rewritten}
    return projection


def _signed_image(image: Any, storage: S3Storage | None) -> dict[str, Any] | None:
    if storage is None or not isinstance(image, dict):
        return None
    key = image.get("object_key")
    version = image.get("storage_version_id")
    media_type = image.get("media_type")
    width = image.get("width")
    height = image.get("height")
    if not all(isinstance(value, str) and value for value in (key, version, media_type)):
        return None
    if not isinstance(width, int) or not isinstance(height, int):
        return None
    try:
        signed = storage.presign_cover(
            key=str(key), version_id=str(version), media_type=str(media_type)
        )
    except StorageError:
        return None
    return {
        "url": signed.url,
        "media_type": str(media_type),
        "width": width,
        "height": height,
        "expires_in_seconds": signed.expires_in_seconds,
    }


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="storefront_not_found")
