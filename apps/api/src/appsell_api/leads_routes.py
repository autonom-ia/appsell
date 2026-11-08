"""Base de leads da loja e o export "para marketing" (#619).

As duas leituras são do banco (migration 0078):
- `read_store_leads` devolve as primeiras 500 linhas e os totais;
- `export_store_leads` devolve só quem pode receber e-mail opcional da loja
  agora. Ela audita e limita a 5 exports por minuto.

Esta camada valida período e produto, com a mesma regra do relatório de
vendas, e traduz o erro do banco em resposta sem detalhe. O CSV é montado no
BFF (`lib/reports/`). A resposta tem e-mail: nada dela vai ao log.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .catalog_routes import _authenticate, _require_seller
from .sales_report_routes import parametros_do_periodo

router = APIRouter(prefix="/v1/me/sellers")

LEITURA = "growth.leads.read"
EXPORT = "growth.leads.export"

_ERROS = {
    "42501": (status.HTTP_403_FORBIDDEN, "forbidden"),
    "22023": (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_period"),
    "P4291": (status.HTTP_429_TOO_MANY_REQUESTS, "export_rate_limited"),
    "P4131": (status.HTTP_413_CONTENT_TOO_LARGE, "export_too_large"),
}


def _erro(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    codigo, detalhe = _ERROS.get(
        str(sqlstate), (status.HTTP_503_SERVICE_UNAVAILABLE, "leads_unavailable")
    )
    return HTTPException(status_code=codigo, detail=detalhe)


def _objeto(valor: object) -> dict[str, Any]:
    if not isinstance(valor, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="leads_unavailable"
        )
    return valor


@router.get("/{seller_id}/leads")
async def read_store_leads(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, LEITURA)
    await _require_seller(internal, seller_id)
    inicio, fim, produto = parametros_do_periodo(request)
    try:
        async with auth.domain_session(internal.envelope) as session:
            valor = await session.scalar(
                text("select app_private.read_store_leads(:actor,:seller,:start,:end,:product)"),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "start": inicio,
                    "end": fim,
                    "product": produto,
                },
            )
    except DBAPIError as error:
        raise _erro(error) from error
    return _objeto(valor)


@router.get("/{seller_id}/leads/export")
async def export_store_leads(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, EXPORT)
    await _require_seller(internal, seller_id)
    inicio, fim, produto = parametros_do_periodo(request)
    try:
        async with auth.domain_session(internal.envelope) as session:
            valor = await session.scalar(
                text(
                    "select app_private.export_store_leads("
                    ":actor,:seller,:start,:end,:product,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "start": inicio,
                    "end": fim,
                    "product": produto,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _erro(error) from error
    return _objeto(valor)
