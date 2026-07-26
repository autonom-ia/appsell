"""Payout-account binding: which provider account receives a seller's money.

The write path is the single most sensitive mutation in the commerce surface:
binding decides WHERE EVERY FUTURE SALE of the seller settles. Three gates
stack, on purpose:

1. The signed internal envelope carries the capability and the seller org the
   BFF vouched for (``service_auth``), and the database functions re-check both
   against the transaction-local context (0021).
2. This router checks MEMBERSHIP explicitly — the envelope only proves the BFF
   echoed the browser's seller id; it does not prove the authenticated user
   belongs to that seller. Reading payout status requires an active
   ``seller_owner``/``seller_admin``; binding requires ``seller_owner``.
3. The provider account id itself is validated against the provider's shape.
   The BFF is trusted to send only ids taken from an authenticated
   ``accounts.create`` response — never from client input (0021's docstring) —
   and this router still refuses anything that does not look like one.

The 55000 refusal from the upsert (an attempt to RE-POINT an existing binding
at a different provider account) is audited via
``app_private.record_payout_repoint_denied`` (0022). The audit must survive the
409 response, and ``domain_session`` rolls back the domain savepoint whenever
an exception crosses it — so the route records the denial INSIDE the session,
exits the context normally (committing the audit), and only then raises.
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
from sqlalchemy.ext.asyncio import AsyncSession

from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1/me/sellers")

_PROVIDERS = frozenset({"stripe"})
# Stripe account ids are `acct_` + alphanumerics. 0021's column constraint
# admits 3..255 printable octets; this is the provider-specific tightening.
_STRIPE_ACCOUNT_PATTERN = re.compile(r"^acct_[A-Za-z0-9]{1,250}$")
_READ_ROLES = ("seller_owner", "seller_admin")
_MANAGE_ROLES = ("seller_owner",)

# Reads the caller's own role through a security-definer function, NOT with a
# direct select on seller_membership: the runtime policy membership_self_select
# (0002) admits that read only under the capability allowlist
# audit.read/audit.write/seller.list/seller.read, so a direct select under
# commerce.payout.read sees zero rows and every request 403s. Found in
# production; the tests missed it because they mocked the session and never
# crossed RLS with the real capability.
_MEMBERSHIP_STATEMENT = text(
    "select app_private.resolve_seller_membership_role(:seller_id) as role"
)


class PayoutAccountBindBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: str
    provider_account_id: str
    charges_enabled: bool = False
    payouts_enabled: bool = False
    details_submitted: bool = False
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


@router.get("/{seller_id}/payout-account")
async def read_payout_account(
    request: Request, seller_id: UUID, provider: str = "stripe"
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "commerce.payout.read")
    await _require_seller(internal, seller_id)
    if provider not in _PROVIDERS:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="unsupported_payout_provider",
        )
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            await _require_membership(session, seller_id, _READ_ROLES)
            row = (
                (
                    await session.execute(
                        text(
                            "select payout_account_id, provider_account_id, "
                            "charges_enabled, payouts_enabled, details_submitted, "
                            "disabled_reason, status_synced_at "
                            "from app_private.resolve_seller_payout_account("
                            ":seller_id, :provider)"
                        ),
                        {"seller_id": seller_id, "provider": provider},
                    )
                )
                .mappings()
                .one_or_none()
            )
    except DBAPIError as error:
        raise _payout_domain_error(error) from error
    if row is None:
        return {"payout_account": None}
    return {
        "payout_account": {
            "payout_account_id": str(row["payout_account_id"]),
            "provider": provider,
            "provider_account_id": row["provider_account_id"],
            "charges_enabled": bool(row["charges_enabled"]),
            "payouts_enabled": bool(row["payouts_enabled"]),
            "details_submitted": bool(row["details_submitted"]),
            "disabled_reason": row["disabled_reason"],
            "status_synced_at": row["status_synced_at"].isoformat(),
        }
    }


@router.post("/{seller_id}/payout-account")
async def bind_payout_account(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "commerce.payout.manage")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(PayoutAccountBindBody, internal)
    envelope = internal.envelope
    repoint_denied = False
    payout_account_id: Any = None
    try:
        async with auth.domain_session(envelope) as session:
            await _require_membership(session, seller_id, _MANAGE_ROLES)
            attempt = await session.begin_nested()
            try:
                payout_account_id = await session.scalar(
                    text(
                        "select app_private.upsert_seller_payout_account("
                        ":seller_id, :provider, :provider_account_id, "
                        ":charges_enabled, :payouts_enabled, :details_submitted, "
                        ":disabled_reason, :request_id)"
                    ),
                    {
                        "seller_id": seller_id,
                        "provider": body.provider,
                        "provider_account_id": body.provider_account_id,
                        "charges_enabled": body.charges_enabled,
                        "payouts_enabled": body.payouts_enabled,
                        "details_submitted": body.details_submitted,
                        "disabled_reason": body.disabled_reason,
                        "request_id": envelope.request_id,
                    },
                )
                await attempt.commit()
            except DBAPIError as error:
                if attempt.is_active:
                    await attempt.rollback()
                if _sqlstate(error) != "55000":
                    raise
                # The denial path writes nothing, so rolling back the attempt
                # savepoint loses nothing. Recording OUTSIDE the savepoint lets
                # the audit row commit with the request even though the bind
                # itself is refused.
                await session.scalar(
                    text(
                        "select app_private.record_payout_repoint_denied("
                        ":seller_id, :provider, :attempted, :request_id)"
                    ),
                    {
                        "seller_id": seller_id,
                        "provider": body.provider,
                        "attempted": body.provider_account_id,
                        "request_id": envelope.request_id,
                    },
                )
                repoint_denied = True
    except DBAPIError as error:
        raise _payout_domain_error(error) from error
    if repoint_denied:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="payout_account_already_bound"
        )
    return {
        "payout_account_id": str(payout_account_id),
        "provider": body.provider,
        "provider_account_id": body.provider_account_id,
    }


async def _require_membership(
    session: AsyncSession,
    seller_id: UUID,
    roles: tuple[str, ...],
) -> None:
    # The envelope's seller id only proves what the BFF echoed from the browser.
    # Membership is what makes the caller ENTITLED to this seller's payout
    # surface. The function reports only the CALLER's own role, and only for the
    # seller the request is already scoped to, so this cannot leak another
    # member — and it refuses (42501) if the scope does not match.
    membership_role = await session.scalar(_MEMBERSHIP_STATEMENT, {"seller_id": seller_id})
    if membership_role not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


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


async def _require_seller(internal: AuthenticatedInternalRequest, seller_id: UUID) -> None:
    if internal.envelope.seller_organization_id != seller_id:
        await internal.consume_rejected_request()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def _model_from_body(
    model: type[PayoutAccountBindBody], internal: AuthenticatedInternalRequest
) -> PayoutAccountBindBody:
    try:
        value = json.loads(internal.body, object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate(value)
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


def _sqlstate(error: DBAPIError) -> str | None:
    sqlstate = getattr(error.orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


def _payout_domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = _sqlstate(error)
    if sqlstate == "23505":
        # Either unique constraint on the table: the seller already bound this
        # provider concurrently, or the provider account is bound to another
        # seller. Neither case reveals WHICH to the caller.
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="payout_account_conflict")
    if sqlstate == "22023":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_payout_request",
        )
    if sqlstate in {"28000", "42501"}:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="payout_unavailable",
    )
