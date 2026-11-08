"""Relatório de vendas da loja por período e produto, e as linhas do CSV (#562 v1).

As duas leituras são do banco (migration 0072): `read_sales_report` agrega por
dia e produto, e `export_sales_rows` devolve uma linha por compra, audita e
limita a 5 exports por minuto. Esta camada só valida o período e o produto,
e traduz o erro do banco em resposta sem detalhe. O CSV é montado no BFF
(`lib/reports/`), que já tem as linhas em JSON.

A v1 conta vendas e estornos, sem valor: o webhook do Financial não traz o
valor (combinado com a trilha Dinheiro).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .catalog_routes import _authenticate, _require_seller

router = APIRouter(prefix="/v1/me/sellers")

LEITURA = "commerce.sales.report.read"
EXPORT = "commerce.sales.report.export"
PERIODO_MAXIMO_DIAS = 366
_TAMANHO_DATA_ISO = 10

_ERROS = {
    "42501": (status.HTTP_403_FORBIDDEN, "forbidden"),
    "22023": (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_period"),
    "P4291": (status.HTTP_429_TOO_MANY_REQUESTS, "export_rate_limited"),
    "P4131": (status.HTTP_413_CONTENT_TOO_LARGE, "export_too_large"),
}


def periodo_da_query(de: str | None, ate: str | None) -> tuple[date, date] | None:
    """Datas `AAAA-MM-DD`, início antes ou igual ao fim, até 366 dias de calendário."""
    if de is None or ate is None or len(de) != _TAMANHO_DATA_ISO or len(ate) != _TAMANHO_DATA_ISO:
        return None
    try:
        inicio, fim = date.fromisoformat(de), date.fromisoformat(ate)
    except ValueError:
        return None
    if fim < inicio or (fim - inicio).days >= PERIODO_MAXIMO_DIAS:
        return None
    return inicio, fim


def parametros_do_periodo(request: Request) -> tuple[date, date, UUID | None]:
    periodo = periodo_da_query(request.query_params.get("from"), request.query_params.get("to"))
    if periodo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_period"
        )
    produto_texto = request.query_params.get("product")
    produto: UUID | None = None
    if produto_texto is not None:
        try:
            produto = UUID(produto_texto)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_product"
            ) from error
    return periodo[0], periodo[1], produto


def _erro(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    codigo, detalhe = _ERROS.get(
        str(sqlstate), (status.HTTP_503_SERVICE_UNAVAILABLE, "sales_report_unavailable")
    )
    return HTTPException(status_code=codigo, detail=detalhe)


def _objeto(valor: object) -> dict[str, Any]:
    if not isinstance(valor, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="sales_report_unavailable"
        )
    return valor


@router.get("/{seller_id}/sales/report")
async def read_sales_report(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, LEITURA)
    await _require_seller(internal, seller_id)
    inicio, fim, produto = parametros_do_periodo(request)
    try:
        async with auth.domain_session(internal.envelope) as session:
            valor = await session.scalar(
                text("select app_private.read_sales_report(:actor,:seller,:start,:end,:product)"),
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


@router.get("/{seller_id}/sales/export")
async def export_sales_rows(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, EXPORT)
    await _require_seller(internal, seller_id)
    inicio, fim, produto = parametros_do_periodo(request)
    try:
        async with auth.domain_session(internal.envelope) as session:
            valor = await session.scalar(
                text(
                    "select app_private.export_sales_rows("
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
