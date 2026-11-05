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
_EVENT_TYPES = frozenset(
    {
        "purchase.approved",
        "purchase.refunded",
        "subscription.cancelled",
    }
    | {
        # Disputa de pagamento (#542): formato do estorno mais os dois campos
        # da disputa, que só esses eventos levam.
        "purchase.dispute_opened",
        "purchase.dispute_won",
        "purchase.dispute_lost",
    }
)
_DISPUTE_EVENT_TYPES = frozenset(
    {"purchase.dispute_opened", "purchase.dispute_won", "purchase.dispute_lost"}
)
_DISPUTE_ID_KEY = "dispute_id"
_DISPUTE_CREATED_KEY = "dispute_created_at"
_DISPUTE_PREFIX = "dp_"
_DISPUTE_ID_MAX = 255
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
    # Só nos eventos de disputa (#542): o id da disputa na Stripe e quando ela
    # foi aberta lá. Ficam None nos demais.
    dispute_id: str | None = None
    dispute_created_at: datetime | None = None


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
    if not isinstance(payload, dict):
        raise WebhookRejectedError("webhook_payload_invalid")
    is_dispute = payload.get("event_type") in _DISPUTE_EVENT_TYPES
    # Disputa: as chaves do estorno (v3) mais o id da disputa, e a data dela.
    # A data pode faltar: a disputa é gravada sem efeito (ver abaixo). Outro
    # evento: as chaves da disputa nunca entram.
    with_dispute_id = expected_v3 | {_DISPUTE_ID_KEY}
    allowed = (
        (with_dispute_id | {_DISPUTE_CREATED_KEY}, with_dispute_id)
        if is_dispute
        else (
            expected_v1,
            expected_v2,
            expected_seller_v2,
            expected_v3,
        )
    )
    if set(payload) not in allowed:
        raise WebhookRejectedError("webhook_payload_invalid")
    has_buyer = "buyer_email" in payload
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
    occurred_at = _utc_instant(occurred)
    seller_id = payload.get("seller_id")
    if seller_id is not None and (
        not isinstance(seller_id, str) or _UUID.fullmatch(seller_id) is None
    ):
        raise WebhookRejectedError("webhook_payload_invalid")
    dispute_id: str | None = None
    dispute_created_at: datetime | None = None
    if is_dispute:
        dispute_id = payload["dispute_id"]
        if not _valid_dispute_id(dispute_id):
            raise WebhookRejectedError("webhook_payload_invalid")
        dispute_created_at = _dispute_created_at(payload.get(_DISPUTE_CREATED_KEY), occurred_at)
    return NormalizedCheckoutEvent(
        event_type,
        product,
        purchase,
        occurred_at,
        buyer_email,
        buyer_identity_sub,
        seller_id,
        dispute_id,
        dispute_created_at,
    )


def _utc_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise WebhookRejectedError("webhook_payload_invalid")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WebhookRejectedError("webhook_payload_invalid") from error
    if instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(instant):
        raise WebhookRejectedError("webhook_payload_invalid")
    return instant


def _dispute_created_at(value: object, occurred_at: datetime) -> datetime | None:
    """A data real da disputa, ou None se faltar ou vier inválida.

    Nunca vira "agora": sem data confiável o Sell grava a disputa sem efeito no
    acesso e a conciliação alerta (`dispute_without_created_at`). Data depois
    do evento também é inválida: a disputa existe antes de qualquer evento dela.
    """
    if value is None:
        return None
    try:
        instant = _utc_instant(value)
    except WebhookRejectedError:
        return None
    return instant if instant <= occurred_at else None


def _valid_dispute_id(value: object) -> bool:
    # Id de disputa da Stripe: "dp_" e letras ou dígitos ASCII.
    if not isinstance(value, str) or not value.startswith(_DISPUTE_PREFIX):
        return False
    body = value.removeprefix(_DISPUTE_PREFIX)
    return len(value) <= _DISPUTE_ID_MAX and body.isascii() and body.isalnum()


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
