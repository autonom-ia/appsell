"""Provider-driven payout status sync (the ``account.updated`` bridge).

The caller is the BFF's webhook route, NOT a browser: the envelope must carry
the dedicated worker identity and capability, and NO seller — the sync function
(0023) resolves the seller from the unique provider-account binding itself.
Refusing a tenant-scoped envelope here (and refusing a tenant-less one in the
user-facing routes) keeps the two write paths mutually unreachable.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1/internal")

_PROVIDERS = frozenset({"stripe"})
_STRIPE_ACCOUNT_PATTERN = re.compile(r"^acct_[A-Za-z0-9]{1,250}$")
# The only identity allowed to drive this route. user_id is derived from
# (issuer, sub) by service_auth's uuid5 rule, so pinning the sub pins the whole
# identity; a stolen credential still cannot reuse a user session's envelope
# here because those carry a seller (refused below) and a different sub.
_SYNC_SUBJECT = "stripe-connect-webhook-v1"
_CHECKOUT_SUBJECT = "stripe-checkout-v1"


class PayoutStatusSyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: str
    provider_account_id: str
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    disabled_reason: str | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in _PROVIDERS:
            raise ValueError("unsupported payout provider")
        return value

    @field_validator("provider_account_id")
    @classmethod
    def validate_provider_account_id(cls, value: str) -> str:
        if not _STRIPE_ACCOUNT_PATTERN.fullmatch(value):
            raise ValueError("invalid provider account id")
        return value

    @field_validator("disabled_reason")
    @classmethod
    def validate_disabled_reason(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not 1 <= len(value.encode("utf-8")) <= 255 or _contains_control(value):
            raise ValueError("invalid disabled reason")
        return value


@router.post("/payout-accounts/sync")
async def sync_payout_account(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "commerce.payout.sync")
    envelope = internal.envelope
    if envelope.seller_organization_id is not None or envelope.identity.sub != _SYNC_SUBJECT:
        await internal.consume_rejected_request()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    body = await _model_from_body(internal)
    try:
        async with auth.domain_session(envelope) as session:
            payout_account_id = await session.scalar(
                text(
                    "select app_private.sync_payout_account_status("
                    ":provider, :provider_account_id, :charges_enabled, "
                    ":payouts_enabled, :details_submitted, :disabled_reason, "
                    ":request_id)"
                ),
                {
                    "provider": body.provider,
                    "provider_account_id": body.provider_account_id,
                    "charges_enabled": body.charges_enabled,
                    "payouts_enabled": body.payouts_enabled,
                    "details_submitted": body.details_submitted,
                    "disabled_reason": body.disabled_reason,
                    "request_id": envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _sync_domain_error(error) from error
    if payout_account_id is None:
        # Unknown account: acknowledged, not an error — the provider's retries
        # cannot make an unbound account bound.
        return {"updated": False}
    return {"updated": True, "payout_account_id": str(payout_account_id)}


@router.get("/sellers/{seller_id}/payout-destination")
async def resolve_payout_destination(request: Request, seller_id: UUID) -> dict[str, Any]:
    # The checkout worker's single question before charging: which provider
    # account receives this seller's money, and can it charge yet. The seller
    # id is a LOOKUP KEY from the storefront the buyer is viewing, not a tenant
    # claim — the envelope must be worker-shaped (no seller, pinned subject).
    auth, internal = await _authenticate(request, "commerce.checkout.destination")
    envelope = internal.envelope
    if envelope.seller_organization_id is not None or envelope.identity.sub != _CHECKOUT_SUBJECT:
        await internal.consume_rejected_request()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select provider_account_id, charges_enabled "
                            "from app_private.resolve_payout_destination("
                            ":seller_id, 'stripe')"
                        ),
                        {"seller_id": seller_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
    except DBAPIError as error:
        raise _sync_domain_error(error) from error
    if row is None:
        return {"payout_destination": None}
    return {
        "payout_destination": {
            "provider": "stripe",
            "provider_account_id": row["provider_account_id"],
            "charges_enabled": bool(row["charges_enabled"]),
        }
    }


async def _authenticate(
    request: Request, capability: str
) -> tuple[InternalRequestAuthenticator, AuthenticatedInternalRequest]:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_auth_not_configured",
        )
    return auth, await auth.authenticate(request, capability)


async def _model_from_body(internal: AuthenticatedInternalRequest) -> PayoutStatusSyncBody:
    try:
        value = json.loads(internal.body, object_pairs_hook=_reject_duplicate_keys)
        return PayoutStatusSyncBody.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_request_body",
        ) from error


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _sync_domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate in {"28000", "42501"}:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if sqlstate == "22023":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_payout_sync_request",
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="payout_sync_unavailable",
    )
