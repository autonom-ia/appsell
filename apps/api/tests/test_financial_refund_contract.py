"""Contract with the Financial refund worker (autonom-ia/autonomia-financial#152).

The Financial signs purchase.refunded exactly like purchase.approved, with its
own event id. The golden vector below is asserted byte for byte on both sides
(Financial: tests/external-checkout-refund-outbox.test.ts), so neither repo can
change the wire format alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from appsell_api.webhook_adapters import (
    WebhookRejectedError,
    parse_generic_hmac_v1,
    verify_generic_hmac_v1,
)
from appsell_api.webhook_routes import _provider_signature

SECRET = bytes([7]) * 32
ENDPOINT_KEY = "endpointkeyendpointkeyendpointkey123456"
ORDER_ID = "6f0d7c1e-8a52-4a4b-9a77-1f2e3d4c5b6a"
SELLER_ID = "4b1f0f5c-1d1f-4f6a-9a3b-2c1d0e9f8a7b"
TIMESTAMP = "1792843200"
REFUND_EVENT_ID = f"financial:external-checkout-refund:{ORDER_ID}"
APPROVED_EVENT_ID = f"financial:external-checkout:{ORDER_ID}"
SIGNATURE = "v1=0fc6d6461f96b300e54dd84fcb56de69da941525e7894c161b0e01a14e797ac7"
PROTOCOL = "external-checkout.webhook.generic-hmac.v1"
BODY = json.dumps(
    {
        "event_type": "purchase.refunded",
        "seller_id": SELLER_ID,
        "external_product_id": "prod-1",
        "external_purchase_id": ORDER_ID,
        "occurred_at": "2026-10-20T10:00:00.000Z",
        "buyer_email": "buyer@example.com",
        "buyer_identity_sub": "sub-1",
    },
    separators=(",", ":"),
).encode()


def _verify(*, event_id: str = REFUND_EVENT_ID, body: bytes = BODY) -> str:
    return verify_generic_hmac_v1(
        secret=SECRET,
        endpoint_key=ENDPOINT_KEY,
        timestamp=TIMESTAMP,
        event_id=event_id,
        signature=SIGNATURE,
        body=body,
        now=int(TIMESTAMP),
        max_clock_skew_seconds=300,
        protocol=PROTOCOL,
    )


def test_golden_vector_from_financial_verifies_and_parses_as_refund() -> None:
    # Catches: the Sell rejecting the exact bytes the Financial worker sends.
    _verify()
    event = parse_generic_hmac_v1(BODY)
    assert event.event_type == "purchase.refunded"
    assert event.external_purchase_reference == ORDER_ID
    assert event.external_product_reference == "prod-1"
    assert event.seller_id == SELLER_ID
    assert event.buyer_email == "buyer@example.com"
    assert event.buyer_identity_sub == "sub-1"
    assert event.occurred_at == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_refund_signature_does_not_validate_as_the_approved_event() -> None:
    # Catches: a refund signature replayable under the approved event id, or the reverse.
    with pytest.raises(WebhookRejectedError):
        _verify(event_id=APPROVED_EVENT_ID)


def test_tampered_body_is_rejected() -> None:
    # Catches: an approved body passing with the refund signature.
    approved = BODY.replace(b'"purchase.refunded"', b'"purchase.approved"')
    with pytest.raises(WebhookRejectedError):
        _verify(body=approved)


def test_financial_headers_select_the_external_checkout_protocol() -> None:
    # Catches: the ingress reading the refund under the legacy appsell protocol.
    request = SimpleNamespace(
        headers={
            "x-external-checkout-webhook-event-id": REFUND_EVENT_ID,
            "x-external-checkout-webhook-timestamp": TIMESTAMP,
            "x-external-checkout-webhook-signature": SIGNATURE,
        }
    )
    assert _provider_signature(request) == (PROTOCOL, REFUND_EVENT_ID, TIMESTAMP, SIGNATURE)  # type: ignore[arg-type]
