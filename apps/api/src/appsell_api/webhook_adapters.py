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
_EVENT_TYPES = frozenset({"purchase.approved", "purchase.refunded", "subscription.cancelled"})


class WebhookRejectedError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedCheckoutEvent:
    event_type: str
    external_product_reference: str
    external_purchase_reference: str
    occurred_at: datetime


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
) -> str:
    if len(secret) != 32 or _EVENT_ID.fullmatch(event_id) is None:
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
    canonical = (
        f"appsell.webhook.generic-hmac.v1\n{endpoint_key}\n{timestamp}\n{event_id}\n{payload_hash}"
    ).encode()
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
    expected = {
        "event_type",
        "external_product_id",
        "external_purchase_id",
        "occurred_at",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise WebhookRejectedError("webhook_payload_invalid")
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
    try:
        occurred_at = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    except ValueError as error:
        raise WebhookRejectedError("webhook_payload_invalid") from error
    if occurred_at.tzinfo is None or occurred_at.utcoffset() != UTC.utcoffset(occurred_at):
        raise WebhookRejectedError("webhook_payload_invalid")
    return NormalizedCheckoutEvent(event_type, product, purchase, occurred_at)


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
