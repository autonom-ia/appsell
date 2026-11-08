"""Consentimento de e-mail da loja pela própria pessoa, logada (#619, #561).

Ponto único para a caixa "quero novidades desta loja", no checkout e em
"Minha conta". Quem grava é `app_private.record_email_consent` (0066):
- o e-mail sai da identidade do contexto, nunca do pedido;
- a versão e o sha do texto ficam como prova;
- a auditoria vai na mesma transação.

Aqui a finalidade é sempre `store_news`, versão 1, e a origem é `checkout` ou
`account`. Mudar o texto é migration com versão nova, e esta constante sobe
junto.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .catalog_routes import _authenticate, _model_from_body, _require_seller

router = APIRouter(prefix="/v1/me/sellers")

CAPACIDADE = "notification.email.consent"
FINALIDADE = "store_news"
VERSAO_DO_TEXTO = 1


class EmailConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["checkout", "account"]


@router.post("/{seller_id}/email-consent", status_code=status.HTTP_204_NO_CONTENT)
async def record_email_consent(request: Request, seller_id: UUID) -> Response:
    auth, internal = await _authenticate(request, CAPACIDADE)
    await _require_seller(internal, seller_id)
    body: EmailConsentBody = await _model_from_body(EmailConsentBody, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            await session.scalar(
                text(
                    "select app_private.record_email_consent("
                    ":seller,:purpose,cast(:version as smallint),:source,:request_id)"
                ),
                {
                    "seller": seller_id,
                    "purpose": FINALIDADE,
                    "version": VERSAO_DO_TEXTO,
                    "source": body.source,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        sqlstate = str(getattr(error.orig, "sqlstate", ""))
        if sqlstate == "28000":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="forbidden"
            ) from error
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="email_consent_unavailable"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
