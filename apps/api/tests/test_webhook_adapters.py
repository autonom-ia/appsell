import base64
import hashlib
import hmac
import json
from typing import Any

import pytest

from appsell_api.config import Settings
from appsell_api.webhook_adapters import (
    WebhookRejectedError,
    parse_generic_hmac_v1,
    verify_generic_hmac_v1,
)
from appsell_api.webhook_secrets import (
    WebhookSecretConfigurationError,
    WebhookSecretResolver,
)


def _key(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


def _body() -> bytes:
    return json.dumps(
        {
            "event_type": "purchase.approved",
            "external_product_id": "course-codex",
            "external_purchase_id": "purchase-001",
            "occurred_at": "2026-07-14T07:00:00Z",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _signature(secret: bytes, endpoint: str, timestamp: str, event_id: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    canonical = (
        f"appsell.webhook.generic-hmac.v1\n{endpoint}\n{timestamp}\n{event_id}\n{digest}"
    ).encode()
    return "v1=" + hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def test_verifies_signature_before_parsing_and_preserves_raw_hash() -> None:
    secret = bytes([9]) * 32
    body = _body()
    signature = _signature(secret, "endpoint-key", "1784012400", "evt-001", body)
    assert (
        verify_generic_hmac_v1(
            secret=secret,
            endpoint_key="endpoint-key",
            timestamp="1784012400",
            event_id="evt-001",
            signature=signature,
            body=body,
            now=1784012400,
            max_clock_skew_seconds=300,
        )
        == hashlib.sha256(body).hexdigest()
    )
    event = parse_generic_hmac_v1(body)
    assert event.event_type == "purchase.approved"
    assert event.external_product_reference == "course-codex"


@pytest.mark.parametrize("mutation", [b" ", b"\n", "ç".encode()])
def test_rejects_any_body_mutation(mutation: bytes) -> None:
    secret = bytes([9]) * 32
    body = _body()
    signature = _signature(secret, "endpoint-key", "1784012400", "evt-001", body)
    with pytest.raises(WebhookRejectedError, match="webhook_rejected"):
        verify_generic_hmac_v1(
            secret=secret,
            endpoint_key="endpoint-key",
            timestamp="1784012400",
            event_id="evt-001",
            signature=signature,
            body=body + mutation,
            now=1784012400,
            max_clock_skew_seconds=300,
        )


def test_rejects_expired_timestamp_and_unknown_payload_fields() -> None:
    secret = bytes([9]) * 32
    body = _body()
    with pytest.raises(WebhookRejectedError, match="webhook_rejected"):
        verify_generic_hmac_v1(
            secret=secret,
            endpoint_key="endpoint-key",
            timestamp="1784012000",
            event_id="evt-001",
            signature=_signature(secret, "endpoint-key", "1784012000", "evt-001", body),
            body=body,
            now=1784012400,
            max_clock_skew_seconds=300,
        )
    invalid = json.loads(body)
    invalid["email"] = "buyer@example.test"
    with pytest.raises(WebhookRejectedError, match="webhook_payload_invalid"):
        parse_generic_hmac_v1(json.dumps(invalid).encode())
    duplicate = (
        b'{"event_type":"purchase.approved","event_type":"purchase.refunded",'
        b'"external_product_id":"course-codex","external_purchase_id":"purchase-001",'
        b'"occurred_at":"2026-07-14T07:00:00Z"}'
    )
    with pytest.raises(WebhookRejectedError, match="webhook_payload_invalid"):
        parse_generic_hmac_v1(duplicate)


def _body_v2(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "event_type": "purchase.approved",
        "external_product_id": "course-codex",
        "external_purchase_id": "purchase-001",
        "occurred_at": "2026-07-14T07:00:00Z",
        "buyer_email": "buyer@example.test",
        "buyer_identity_sub": None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def test_parses_v2_payload_with_buyer_identity() -> None:
    event = parse_generic_hmac_v1(_body_v2())
    assert event.buyer_email == "buyer@example.test"
    assert event.buyer_identity_sub is None
    with_sub = parse_generic_hmac_v1(
        _body_v2(buyer_identity_sub="04787418-5001-7006-d984-3a8937060228")
    )
    assert with_sub.buyer_identity_sub == "04787418-5001-7006-d984-3a8937060228"
    legacy = parse_generic_hmac_v1(_body())
    assert legacy.buyer_email is None
    assert legacy.buyer_identity_sub is None


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "Buyer@Example.test",
        " buyer@example.test",
        "buyer example@test.com",
        "no-at-sign.example.test",
        "@example.test",
        "a@b",
        "b" * 240 + "@example.test",
        "buyer@example.test\x00",
    ],
)
def test_rejects_invalid_v2_buyer_email(email: str | None) -> None:
    with pytest.raises(WebhookRejectedError, match="webhook_payload_invalid"):
        parse_generic_hmac_v1(_body_v2(buyer_email=email))


@pytest.mark.parametrize(
    "sub",
    ["", "ab", " padded ", "x" * 256, "linha\nquebrada", 42, "registration:victim@example.test"],
)
def test_rejects_invalid_v2_buyer_identity_sub(sub: Any) -> None:
    with pytest.raises(WebhookRejectedError, match="webhook_payload_invalid"):
        parse_generic_hmac_v1(_body_v2(buyer_identity_sub=sub))


def test_rejects_partial_v2_key_sets() -> None:
    payload = json.loads(_body_v2())
    del payload["buyer_identity_sub"]
    with pytest.raises(WebhookRejectedError, match="webhook_payload_invalid"):
        parse_generic_hmac_v1(json.dumps(payload).encode())


def test_local_resolver_rejects_secret_reuse_and_non_local_configuration() -> None:
    internal = _key(1)
    base: dict[str, Any] = dict(
        internal_issuer="appsell-bff",
        internal_audience="appsell-api",
        identity_issuer="https://identity.example",
        internal_hmac_keyring_b64_json=json.dumps(
            [{"id": "internal-v1", "key": internal, "active": True}]
        ),
        context_hmac_key_b64=_key(2),
        service_credential_b64=_key(3),
    )
    settings = Settings(
        **base,
        webhook_local_keyring_b64_json=json.dumps(
            [{"reference": "checkout-demo-v1", "key": _key(4)}]
        ),
    )
    assert (
        WebhookSecretResolver.from_settings(settings).resolve("checkout-demo-v1") == bytes([4]) * 32
    )
    reused = Settings(
        **base,
        webhook_local_keyring_b64_json=json.dumps(
            [{"reference": "checkout-demo-v1", "key": internal}]
        ),
    )
    with pytest.raises(WebhookSecretConfigurationError, match="independent"):
        WebhookSecretResolver.from_settings(reused)
    duplicate_material = Settings(
        **base,
        webhook_local_keyring_b64_json=json.dumps(
            [
                {"reference": "checkout-demo-v1", "key": _key(4)},
                {"reference": "checkout-other-v1", "key": _key(4)},
            ]
        ),
    )
    with pytest.raises(WebhookSecretConfigurationError, match="material must be unique"):
        WebhookSecretResolver.from_settings(duplicate_material)
    forbidden_bff_hash = hashlib.sha256(bytes([4]) * 32).hexdigest()
    forbidden_bff = Settings(
        **base,
        webhook_forbidden_key_sha256_json=json.dumps([forbidden_bff_hash]),
        webhook_local_keyring_b64_json=json.dumps(
            [{"reference": "checkout-demo-v1", "key": _key(4)}]
        ),
    )
    with pytest.raises(WebhookSecretConfigurationError, match="independent"):
        WebhookSecretResolver.from_settings(forbidden_bff)
    with pytest.raises(ValueError, match="forbidden outside local/CI"):
        Settings(
            **base,
            environment="staging",
            webhook_local_keyring_b64_json=json.dumps(
                [{"reference": "checkout-demo-v1", "key": _key(4)}]
            ),
        )
