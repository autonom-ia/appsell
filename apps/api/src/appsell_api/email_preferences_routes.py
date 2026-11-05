"""Descadastro de e-mail opcional em um clique, sem login (#561, RFC 8058).

Quem chama é o BFF, com a credencial de serviço. O token é a autorização: o
banco guarda só o sha256 dele, e `app_private.unsubscribe_email` revoga o
consentimento e grava a supressão na mesma transação.

A resposta é a mesma para token válido, desconhecido, já usado ou fora do
formato: quem tenta adivinhar não aprende nada. O token nunca vai ao log.
"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .internal_requests import InternalRequestAuthenticator

router = APIRouter(prefix="/v1/public/email")
logger = structlog.get_logger(__name__)

MAX_CORPO_BYTES = 1024
TOKEN_MIN = 16
TOKEN_MAX = 128


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_email(request: Request) -> Response:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if auth is None or session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="unsubscribe_unavailable"
        )
    auth.require_service_credential(request)
    corpo = await request.body()
    if len(corpo) > MAX_CORPO_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="body_too_large")
    token = _token_do_corpo(corpo)
    if token is None:
        logger.info("email_unsubscribe", outcome="malformed")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        async with session_factory() as session, session.begin():
            encontrado = await session.scalar(
                text("select app_private.unsubscribe_email(:token)"), {"token": token}
            )
    except DBAPIError as error:
        logger.warning("email_unsubscribe_unavailable", error_type=type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="unsubscribe_unavailable"
        ) from error
    logger.info("email_unsubscribe", outcome="applied" if encontrado is True else "unknown")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _token_do_corpo(corpo: bytes) -> str | None:
    try:
        dados = json.loads(corpo.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(dados, dict) or set(dados) != {"token"}:
        return None
    token = dados["token"]
    if not isinstance(token, str) or not TOKEN_MIN <= len(token) <= TOKEN_MAX:
        return None
    return token
