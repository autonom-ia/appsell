from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .internal_requests import InternalRequestAuthenticator
from .webhook_adapters import (
    WebhookRejectedError,
    parse_generic_hmac_v1,
    verify_generic_hmac_v1,
)
from .webhook_secrets import WebhookSecretResolver

router = APIRouter(prefix="/v1/webhooks/checkouts")
_ENDPOINT_KEY = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


@router.post("/{endpoint_key}", status_code=status.HTTP_202_ACCEPTED)
async def accept_checkout_webhook(endpoint_key: str, request: Request) -> dict[str, Any]:
    if _ENDPOINT_KEY.fullmatch(endpoint_key) is None:
        raise _rejected()
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise _rejected()
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    secrets: WebhookSecretResolver | None = getattr(request.app.state, "webhook_secrets", None)
    if auth is None or secrets is None:
        raise _unavailable()
    internal = await auth.authenticate(request, "commerce.webhook.ingest")
    if internal.envelope.seller_organization_id is not None:
        await internal.consume_rejected_request()
        raise _rejected()

    endpoint_hash = hashlib.sha256(endpoint_key.encode()).hexdigest()
    try:
        async with auth.domain_session(internal.envelope) as session:
            resolution = (
                (
                    await session.execute(
                        text("select * from app_private.resolve_webhook_endpoint(:endpoint_hash)"),
                        {"endpoint_hash": endpoint_hash},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if resolution is None:
                raise _rejected()
            secret = secrets.resolve(str(resolution["secret_reference"]))
            if secret is None:
                raise _unavailable()
            protocol, event_id, timestamp_header, signature = _provider_signature(request)
            payload_hash = verify_generic_hmac_v1(
                secret=secret,
                endpoint_key=endpoint_key,
                timestamp=timestamp_header,
                event_id=event_id,
                signature=signature,
                body=internal.body,
                now=int(time.time()),
                max_clock_skew_seconds=int(resolution["max_clock_skew_seconds"]),
                protocol=protocol,
            )
            if resolution["adapter"] != "generic_hmac_v1":
                raise _unavailable()
            normalized = parse_generic_hmac_v1(internal.body)
            result = (
                (
                    await session.execute(
                        text(
                            "select * from app_private.accept_checkout_webhook("
                            "cast(:seller_id as uuid),cast(:endpoint_id as uuid),"
                            "cast(:endpoint_hash as text),cast(:secret_reference as text),"
                            "cast(:endpoint_revision as bigint),"
                            "cast(:event_hash as text),cast(:event_octets as smallint),"
                            "cast(:payload_hash as text),cast(:payload_octets as integer),"
                            "cast(:event_type as text),cast(:product_hash as text),"
                            "cast(:product_octets as smallint),cast(:purchase_hash as text),"
                            "cast(:purchase_octets as smallint),cast(:occurred_at as timestamptz),"
                            "cast(:signed_at_epoch as bigint),cast(:request_id as uuid),"
                            "cast(:buyer_email as text),cast(:buyer_identity_sub as text))"
                        ),
                        {
                            "seller_id": resolution["seller_organization_id"],
                            "endpoint_id": resolution["webhook_endpoint_id"],
                            "endpoint_hash": endpoint_hash,
                            "secret_reference": resolution["secret_reference"],
                            "endpoint_revision": resolution["endpoint_revision"],
                            "event_hash": _sha256(event_id),
                            "event_octets": len(event_id.encode()),
                            "payload_hash": payload_hash,
                            "payload_octets": len(internal.body),
                            "event_type": normalized.event_type,
                            "product_hash": _sha256(normalized.external_product_reference),
                            "product_octets": len(normalized.external_product_reference.encode()),
                            "purchase_hash": _sha256(normalized.external_purchase_reference),
                            "purchase_octets": len(normalized.external_purchase_reference.encode()),
                            "occurred_at": normalized.occurred_at,
                            "signed_at_epoch": int(timestamp_header),
                            "request_id": internal.envelope.request_id,
                            "buyer_email": normalized.buyer_email,
                            "buyer_identity_sub": normalized.buyer_identity_sub,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except WebhookRejectedError as error:
        if str(error) == "webhook_payload_invalid":
            raise HTTPException(status_code=422, detail="webhook_payload_invalid") from error
        raise _rejected() from error
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise HTTPException(status_code=409, detail="webhook_event_conflict") from error
        raise _unavailable() from error
    except HTTPException:
        raise
    except DBAPIError as error:
        raise _unavailable() from error
    return {
        "status": "accepted" if result["created"] else "replayed",
        "webhook_event_id": str(result["webhook_event_id"]),
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _provider_signature(request: Request) -> tuple[str, str, str, str]:
    external_event_id = request.headers.get("x-external-checkout-webhook-event-id")
    external_timestamp = request.headers.get("x-external-checkout-webhook-timestamp")
    external_signature = request.headers.get("x-external-checkout-webhook-signature")
    if external_event_id is not None or external_timestamp is not None or external_signature is not None:
        return (
            "external-checkout.webhook.generic-hmac.v1",
            external_event_id or "",
            external_timestamp or "",
            external_signature or "",
        )
    return (
        "appsell.webhook.generic-hmac.v1",
        request.headers.get("x-appsell-webhook-event-id", ""),
        request.headers.get("x-appsell-webhook-timestamp", ""),
        request.headers.get("x-appsell-webhook-signature", ""),
    )


def _rejected() -> HTTPException:
    return HTTPException(status_code=401, detail="webhook_rejected")


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="webhook_unavailable")
