"""Pedido do checkout guardado no servidor (#154, fatia 1).

Chamada só pelo `create-session` do BFF, para as lojas do piloto
`APPSELL_PINNED_ORDER_SELLER_IDS`. O envelope é do worker do checkout: sujeito
fixo e nenhuma loja, como o destino do repasse. A loja na URL é a chave de
busca vinda da vitrine, não um claim; o banco confere que a oferta é a padrão à
venda daquela loja (0077).

O comprador e o intent chegam em claro pelo canal interno e vão ao banco só em
sha256. Nenhum dos dois vai ao log.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .internal_requests import AuthenticatedInternalRequest
from .payout_sync_routes import _authenticate, _reject_duplicate_keys

router = APIRouter(prefix="/v1/internal")
logger = structlog.get_logger(__name__)

CAPACIDADE = "commerce.checkout.order"
# A única identidade que grava pedido. O user_id sai de (issuer, sub), então
# fixar o sub fixa a identidade; um envelope de usuário traz loja e outro sub.
SUJEITO = "checkout-order-v1"
# O teto do preço da oferta (0018, `catalog_routes._MAX_UNIT_AMOUNT_MINOR`).
MAX_MINOR = 99_999_999_999
REFERENCIA_MAXIMA = 200

_ERROS = {
    "P4095": (status.HTTP_409_CONFLICT, "price_changed"),
    "28000": (status.HTTP_403_FORBIDDEN, "forbidden"),
    "42501": (status.HTTP_403_FORBIDDEN, "forbidden"),
    "22023": (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_checkout_order"),
    "23514": (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_checkout_order"),
    "23505": (status.HTTP_409_CONFLICT, "checkout_intent_already_attached"),
}

_Minor = Field(ge=0, le=MAX_MINOR)


class CheckoutOrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    publication_id: UUID
    unit_amount_minor: StrictInt = Field(ge=1, le=MAX_MINOR)
    currency: Literal["BRL"]
    billing_period: Literal["one_time"]
    platform_fee_minor: StrictInt | None = _Minor
    seller_net_minor: StrictInt | None = _Minor
    plan_key: Literal["launch", "growth", "scale"]
    buyer_identity_sub: StrictStr = Field(min_length=3, max_length=255)


class CheckoutIntentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkout_intent_id: StrictStr = Field(min_length=1, max_length=REFERENCIA_MAXIMA)


def _sha256(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


async def _worker(request: Request) -> AuthenticatedInternalRequest:
    _, internal = await _authenticate(request, CAPACIDADE)
    envelope = internal.envelope
    if envelope.seller_organization_id is not None or envelope.identity.sub != SUJEITO:
        await internal.consume_rejected_request()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return internal


async def _corpo[Modelo: BaseModel](
    internal: AuthenticatedInternalRequest, modelo: type[Modelo]
) -> Modelo:
    try:
        valor = json.loads(internal.body, object_pairs_hook=_reject_duplicate_keys)
        return modelo.model_validate(valor)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_request_body"
        ) from error


def _erro(error: DBAPIError, operacao: str) -> HTTPException:
    sqlstate = str(getattr(error.orig, "sqlstate", None))
    codigo, detalhe = _ERROS.get(
        sqlstate, (status.HTTP_503_SERVICE_UNAVAILABLE, "checkout_order_unavailable")
    )
    logger.warning("checkout_order_refused", operation=operacao, sqlstate=sqlstate)
    return HTTPException(status_code=codigo, detail=detalhe)


@router.post("/sellers/{seller_id}/checkout-orders", status_code=status.HTTP_201_CREATED)
async def create_checkout_order(request: Request, seller_id: UUID) -> dict[str, Any]:
    internal = await _worker(request)
    corpo = await _corpo(internal, CheckoutOrderBody)
    auth = request.app.state.internal_auth
    try:
        async with auth.domain_session(internal.envelope) as session:
            linha = (
                (
                    await session.execute(
                        text(
                            "select order_id, offer_id, offer_version, charged_minor, expires_at "
                            "from app_private.create_checkout_order(:seller,:product,"
                            ":publication,:unit,:currency,:period,:fee,:net,:plan,:buyer_sha256)"
                        ),
                        {
                            "seller": seller_id,
                            "product": corpo.product_id,
                            "publication": corpo.publication_id,
                            "unit": corpo.unit_amount_minor,
                            "currency": corpo.currency,
                            "period": corpo.billing_period,
                            "fee": corpo.platform_fee_minor,
                            "net": corpo.seller_net_minor,
                            "plan": corpo.plan_key,
                            "buyer_sha256": _sha256(corpo.buyer_identity_sub),
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
    except DBAPIError as error:
        raise _erro(error, "create") from error
    if linha is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="checkout_order_unavailable"
        )
    logger.info("checkout_order_recorded", seller_id=str(seller_id))
    return {
        "order_id": str(linha["order_id"]),
        "offer_id": str(linha["offer_id"]),
        "offer_version": int(linha["offer_version"]),
        "charged_minor": int(linha["charged_minor"]),
        "expires_at": linha["expires_at"].isoformat(),
    }


@router.post(
    "/sellers/{seller_id}/checkout-orders/{order_id}/intent",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def attach_checkout_intent(request: Request, seller_id: UUID, order_id: UUID) -> Response:
    internal = await _worker(request)
    corpo = await _corpo(internal, CheckoutIntentBody)
    auth = request.app.state.internal_auth
    try:
        async with auth.domain_session(internal.envelope) as session:
            ligado = await session.scalar(
                text("select app_private.attach_checkout_intent(:seller,:order,:purchase_sha256)"),
                {
                    "seller": seller_id,
                    "order": order_id,
                    "purchase_sha256": _sha256(corpo.checkout_intent_id),
                },
            )
    except DBAPIError as error:
        raise _erro(error, "attach") from error
    if ligado is not True:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="checkout_order_not_open")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
