from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

_EVENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_REFERENCE = re.compile(r"^[^\x00-\x1f\x7f]{1,255}$")
_SIGNATURE = re.compile(r"^v1=([0-9a-f]{64})$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_EVENT_TYPES = frozenset({"purchase.approved", "purchase.refunded", "subscription.cancelled"})
_LEGACY_PROTOCOL = "appsell.webhook.generic-hmac.v1"
_EXTERNAL_CHECKOUT_PROTOCOL = "external-checkout.webhook.generic-hmac.v1"
_SUPPORTED_PROTOCOLS = frozenset({_LEGACY_PROTOCOL, _EXTERNAL_CHECKOUT_PROTOCOL})


class WebhookRejectedError(ValueError):
    pass


_EMAIL_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f\s]")
_SUB_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class NormalizedCheckoutEvent:
    event_type: str
    external_product_reference: str
    external_purchase_reference: str
    occurred_at: datetime
    # Payload v2 (fulfillment): buyer identity. Both stay None on payload v1.
    buyer_email: str | None = None
    buyer_identity_sub: str | None = None
    seller_id: str | None = None


def verify_generic_hmac_v1(
    *,
    secret: bytes,
    endpoint_key: str,
    timestamp: str,
    event_id: str,
    signature: str,
    body: bytes,
    now: int,
    max_clock_skew_seconds: int,
    protocol: str = _LEGACY_PROTOCOL,
) -> str:
    if (
        len(secret) != 32
        or protocol not in _SUPPORTED_PROTOCOLS
        or _EVENT_ID.fullmatch(event_id) is None
    ):
        raise WebhookRejectedError("webhook_rejected")
    try:
        timestamp_value = int(timestamp)
    except ValueError as error:
        raise WebhookRejectedError("webhook_rejected") from error
    if (
        not 30 <= max_clock_skew_seconds <= 900
        or str(timestamp_value) != timestamp
        or timestamp_value < 1
        or abs(now - timestamp_value) > max_clock_skew_seconds
    ):
        raise WebhookRejectedError("webhook_rejected")
    match = _SIGNATURE.fullmatch(signature)
    if match is None:
        raise WebhookRejectedError("webhook_rejected")
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{protocol}\n{endpoint_key}\n{timestamp}\n{event_id}\n{payload_hash}".encode()
    expected = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(match.group(1), expected):
        raise WebhookRejectedError("webhook_rejected")
    return payload_hash


def parse_generic_hmac_v1(body: bytes) -> NormalizedCheckoutEvent:
    try:
        payload = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WebhookRejectedError("webhook_payload_invalid") from error
    expected_v1 = {
        "event_type",
        "external_product_id",
        "external_purchase_id",
        "occurred_at",
    }
    expected_v2 = expected_v1 | {"buyer_email", "buyer_identity_sub"}
    expected_seller_v2 = expected_v1 | {"seller_id"}
    expected_v3 = expected_v2 | {"seller_id"}
    if not isinstance(payload, dict) or set(payload) not in (
        expected_v1,
        expected_v2,
        expected_seller_v2,
        expected_v3,
    ):
        raise WebhookRejectedError("webhook_payload_invalid")
    has_buyer = set(payload) in (expected_v2, expected_v3)
    event_type = payload["event_type"]
    product = payload["external_product_id"]
    purchase = payload["external_purchase_id"]
    occurred = payload["occurred_at"]
    if (
        not isinstance(event_type, str)
        or event_type not in _EVENT_TYPES
        or not _valid_reference(product)
        or not _valid_reference(purchase)
        or not isinstance(occurred, str)
    ):
        raise WebhookRejectedError("webhook_payload_invalid")
    buyer_email: str | None = None
    buyer_identity_sub: str | None = None
    if has_buyer:
        buyer_email = payload["buyer_email"]
        buyer_identity_sub = payload["buyer_identity_sub"]
        if not _valid_buyer_email(buyer_email) or not _valid_buyer_sub(buyer_identity_sub):
            raise WebhookRejectedError("webhook_payload_invalid")
    try:
        occurred_at = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    except ValueError as error:
        raise WebhookRejectedError("webhook_payload_invalid") from error
    if occurred_at.tzinfo is None or occurred_at.utcoffset() != UTC.utcoffset(occurred_at):
        raise WebhookRejectedError("webhook_payload_invalid")
    seller_id = payload.get("seller_id")
    if seller_id is not None and (
        not isinstance(seller_id, str) or _UUID.fullmatch(seller_id) is None
    ):
        raise WebhookRejectedError("webhook_payload_invalid")
    return NormalizedCheckoutEvent(
        event_type, product, purchase, occurred_at, buyer_email, buyer_identity_sub, seller_id
    )


def _valid_buyer_email(value: object) -> bool:
    # Mirrors the SQL checks on fulfillment_job.buyer_email_normalized; 242-octet
    # cap keeps the derived "registration:<email>" identity_sub under 255 chars.
    return (
        isinstance(value, str)
        and value == value.strip().lower()
        and value.find("@") > 0
        and 6 <= len(value.encode("utf-8")) <= 242
        and _EMAIL_FORBIDDEN.search(value) is None
    )


def _valid_buyer_sub(value: object) -> bool:
    if value is None:
        return True
    # "registration:" is the worker-derived namespace (registration:<email>);
    # an emitter must never claim it explicitly, or it could bind a purchase to
    # the identity derived from a DIFFERENT buyer's e-mail.
    return (
        isinstance(value, str)
        and value == value.strip()
        and 3 <= len(value) <= 255
        and _SUB_FORBIDDEN.search(value) is None
        and not value.startswith("registration:")
    )


def _valid_reference(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and len(value.encode("utf-8")) <= 255
        and _REFERENCE.fullmatch(value) is not None
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WebhookRejectedError("webhook_payload_invalid")
        result[key] = value
    return result
