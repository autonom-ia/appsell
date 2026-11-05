"""Links UTM da loja, atribuição da venda e contagem do consentimento (#563).

O painel da loja (dono ou admin) cria, lista e arquiva links e lê as vendas por
campanha. Essas rotas usam o envelope assinado, como o resto do `/v1/me`.

Duas rotas públicas são chamadas só pelo BFF, com a credencial de serviço:

- a atribuição, no início do checkout, depois de o BFF conferir o aceite da
  versão atual do texto do banner e a janela de 7 dias do cookie. O BFF manda
  a referência da compra, e o sha256 é calculado aqui, como no ingress do
  webhook: é por ele que a venda aprovada encontra a campanha;
- a contagem do aceite ou da recusa, sem dado pessoal.

As duas respondem 204 em qualquer caso válido, e a referência da compra nunca
vai ao log.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .catalog_routes import _authenticate, _model_from_body, _require_seller
from .internal_requests import InternalRequestAuthenticator

router = APIRouter(prefix="/v1/me/sellers")
public_router = APIRouter(prefix="/v1/public/tracking")
logger = structlog.get_logger(__name__)

LEITURA = "growth.campaign_link.read"
ESCRITA = "growth.campaign_link.write"
PERIODO_MAXIMO_DIAS = 366
TAMANHO_UTM = 100
TAMANHO_DATA_ISO = 10
MAX_CORPO_PUBLICO_BYTES = 2048
REFERENCIA_MAXIMA = 200

_ERROS = {
    "42501": (status.HTTP_403_FORBIDDEN, "forbidden"),
    "22023": (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_campaign_link"),
    "P4130": (status.HTTP_409_CONFLICT, "too_many_campaign_links"),
}

_Utm = Field(default=None, min_length=1, max_length=TAMANHO_UTM)


class CampaignLinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    destination_page_slug: str | None = Field(default=None, min_length=1, max_length=120)
    utm_source: str = Field(min_length=1, max_length=TAMANHO_UTM)
    utm_medium: str | None = _Utm
    utm_campaign: str | None = _Utm
    utm_content: str | None = _Utm
    utm_term: str | None = _Utm


def _erro(error: DBAPIError, padrao: str) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    codigo, detalhe = _ERROS.get(str(sqlstate), (status.HTTP_503_SERVICE_UNAVAILABLE, padrao))
    return HTTPException(status_code=codigo, detail=detalhe)


def _objeto(valor: object) -> dict[str, Any]:
    if not isinstance(valor, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="campaign_links_unavailable"
        )
    return valor


def periodo_da_query(de: str | None, ate: str | None) -> tuple[date, date] | None:
    """Datas `AAAA-MM-DD`, início antes ou igual ao fim, até 366 dias de calendário."""
    if de is None or ate is None or len(de) != TAMANHO_DATA_ISO or len(ate) != TAMANHO_DATA_ISO:
        return None
    try:
        inicio, fim = date.fromisoformat(de), date.fromisoformat(ate)
    except ValueError:
        return None
    if fim < inicio or (fim - inicio).days >= PERIODO_MAXIMO_DIAS:
        return None
    return inicio, fim


@router.get("/{seller_id}/campaign-links")
async def list_campaign_links(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, LEITURA)
    await _require_seller(internal, seller_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            valor = await session.scalar(
                text("select app_private.list_campaign_links(:actor,:seller)"),
                {"actor": internal.envelope.identity.user_id, "seller": seller_id},
            )
    except DBAPIError as error:
        raise _erro(error, "campaign_links_unavailable") from error
    return _objeto(valor)


@router.post("/{seller_id}/campaign-links", status_code=status.HTTP_201_CREATED)
async def create_campaign_link(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, ESCRITA)
    await _require_seller(internal, seller_id)
    body: CampaignLinkBody = await _model_from_body(CampaignLinkBody, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            valor = await session.scalar(
                text(
                    "select app_private.create_campaign_link(:actor,:seller,:name,:destination,"
                    ":source,:medium,:campaign,:content,:term,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "name": body.name,
                    "destination": body.destination_page_slug,
                    "source": body.utm_source,
                    "medium": body.utm_medium,
                    "campaign": body.utm_campaign,
                    "content": body.utm_content,
                    "term": body.utm_term,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _erro(error, "campaign_links_unavailable") from error
    return _objeto(valor)


@router.post(
    "/{seller_id}/campaign-links/{link_id}/archive", status_code=status.HTTP_204_NO_CONTENT
)
async def archive_campaign_link(request: Request, seller_id: UUID, link_id: UUID) -> Response:
    auth, internal = await _authenticate(request, ESCRITA)
    await _require_seller(internal, seller_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            arquivado = await session.scalar(
                text("select app_private.archive_campaign_link(:actor,:seller,:link,:request_id)"),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "link": link_id,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _erro(error, "campaign_links_unavailable") from error
    if arquivado is not True:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{seller_id}/campaign-links/report")
async def read_campaign_sales(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, LEITURA)
    await _require_seller(internal, seller_id)
    periodo = periodo_da_query(request.query_params.get("from"), request.query_params.get("to"))
    if periodo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_period"
        )
    try:
        async with auth.domain_session(internal.envelope) as session:
            valor = await session.scalar(
                text("select app_private.read_campaign_sales(:actor,:seller,:start,:end)"),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "start": periodo[0],
                    "end": periodo[1],
                },
            )
    except DBAPIError as error:
        raise _erro(error, "campaign_links_unavailable") from error
    return _objeto(valor)


# --- rotas públicas, só para o BFF com a credencial de serviço ---------------


class CheckoutAttributionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_organization_id: UUID
    purchase_reference: str = Field(min_length=1, max_length=REFERENCIA_MAXIMA)
    link_id: UUID | None = None
    utm_source: str | None = _Utm
    utm_medium: str | None = _Utm
    utm_campaign: str | None = _Utm
    utm_content: str | None = _Utm
    utm_term: str | None = _Utm
    visited_at: int = Field(ge=0)


class CookieConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_organization_id: UUID
    text_version: str = Field(min_length=1, max_length=16)
    accepted: bool


async def _corpo_publico(request: Request, model: type[BaseModel]) -> tuple[Any, BaseModel | None]:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if auth is None or session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracking_unavailable"
        )
    auth.require_service_credential(request)
    corpo = await request.body()
    if len(corpo) > MAX_CORPO_PUBLICO_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="body_too_large")
    try:
        return session_factory, model.model_validate(json.loads(corpo.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return session_factory, None


@public_router.post("/checkout-attribution", status_code=status.HTTP_204_NO_CONTENT)
async def record_checkout_attribution(request: Request) -> Response:
    session_factory, body = await _corpo_publico(request, CheckoutAttributionBody)
    if not isinstance(body, CheckoutAttributionBody):
        logger.info("checkout_attribution", outcome="malformed")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    sha = hashlib.sha256(body.purchase_reference.encode()).hexdigest()
    try:
        async with session_factory() as session, session.begin():
            gravada = await session.scalar(
                text(
                    "select app_private.record_checkout_attribution(:seller,:sha,:link,:source,"
                    ":medium,:campaign,:content,:term,:visited_at)"
                ),
                {
                    "seller": body.seller_organization_id,
                    "sha": sha,
                    "link": body.link_id,
                    "source": body.utm_source,
                    "medium": body.utm_medium,
                    "campaign": body.utm_campaign,
                    "content": body.utm_content,
                    "term": body.utm_term,
                    "visited_at": datetime.fromtimestamp(body.visited_at, tz=UTC),
                },
            )
    except (DBAPIError, OverflowError, OSError) as error:
        logger.warning("checkout_attribution_unavailable", error_type=type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracking_unavailable"
        ) from error
    logger.info("checkout_attribution", outcome="recorded" if gravada is True else "ignored")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public_router.post("/cookie-consent", status_code=status.HTTP_204_NO_CONTENT)
async def record_cookie_consent(request: Request) -> Response:
    session_factory, body = await _corpo_publico(request, CookieConsentBody)
    if not isinstance(body, CookieConsentBody):
        logger.info("cookie_consent", outcome="malformed")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        async with session_factory() as session, session.begin():
            await session.scalar(
                text("select app_private.record_cookie_consent(:seller,:version,:accepted)"),
                {
                    "seller": body.seller_organization_id,
                    "version": body.text_version,
                    "accepted": body.accepted,
                },
            )
    except DBAPIError as error:
        logger.warning("cookie_consent_unavailable", error_type=type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracking_unavailable"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
