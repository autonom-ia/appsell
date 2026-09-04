import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from appsell_api.service_auth import (
    ServiceAuthConfigurationError,
    ServiceAuthError,
    ServiceAuthVerifier,
)

NOW = 1_800_000_000
ISSUER = "appsell-bff"
AUDIENCE = "appsell-domain-api"
IDENTITY_ISSUER = "https://identity.autonomia.site"
KEY_ID = "local-v1"
SIGNING_KEY = bytes(range(32))
CONTEXT_KEY = bytes(range(32, 64))
SIGNING_KEY_B64 = base64.b64encode(SIGNING_KEY).decode()
CONTEXT_KEY_B64 = base64.b64encode(CONTEXT_KEY).decode()
BODY = b'{"slug":"studio-x","display_name":"Studio X"}'
GOLDEN_TOKEN_SHA256 = "de925acc1243466d2c502992f29c767781b7ec966c7458ab23b9085b2c20c74c"  # noqa: S105 - public vector digest


def verifier(**overrides: Any) -> ServiceAuthVerifier:
    values: dict[str, Any] = {
        "expected_issuer": ISSUER,
        "expected_audience": AUDIENCE,
        "expected_identity_issuer": IDENTITY_ISSUER,
        "keyring": {KEY_ID: SIGNING_KEY_B64},
        "context_hmac_key": CONTEXT_KEY_B64,
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return ServiceAuthVerifier(**values)


def payload(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "version": 1,
        "kid": KEY_ID,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "session_ref": "8454c385f13913b71120adbfd13ea174924120bd8074dfc8a29af5c7aa9e975e",
        "session_epoch": 1,
        "identity": {
            "issuer": IDENTITY_ISSUER,
            "sub": "github|12345",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        "capability": "seller.onboard",
        "seller_organization_id": None,
        "method": "POST",
        "path": "/v1/me/sellers",
        "query_sha256": hashlib.sha256(b"").hexdigest(),
        "body_sha256": hashlib.sha256(BODY).hexdigest(),
        "request_id": "018f47c2-4d4f-4d5b-8c6a-0f3d9ec6bc14",
        "iat": NOW,
        "exp": NOW + 30,
    }
    value.update(overrides)
    return value


def encode(value: Mapping[str, Any], *, key: bytes = SIGNING_KEY) -> str:
    serialized = json.dumps(value, separators=(",", ":")).encode()
    payload_segment = base64.urlsafe_b64encode(serialized).rstrip(b"=").decode()
    signing_input = f"appsell.internal.v1.{payload_segment}".encode("ascii")
    signature = base64.urlsafe_b64encode(hmac.digest(key, signing_input, "sha256"))
    return f"v1.{payload_segment}.{signature.rstrip(b'=').decode()}"


def verify_token(token: str, **overrides: Any) -> Any:
    return verifier().verify_request(
        token,
        method=overrides.get("method", "POST"),
        path=overrides.get("path", "/v1/me/sellers"),
        query=overrides.get("query", b""),
        body=overrides.get("body", BODY),
        expected_capability=overrides.get("expected_capability", "seller.onboard"),
    )


def test_golden_vector_is_deterministic_and_verifies() -> None:
    token = encode(payload())
    assert hashlib.sha256(token.encode()).hexdigest() == GOLDEN_TOKEN_SHA256

    envelope = verify_token(token)

    assert envelope.identity.user_id == UUID("192098ec-0d8a-5c68-9606-bcee42ec94d7")
    assert envelope.capability == "seller.onboard"
    assert envelope.seller_organization_id is None
    assert envelope.request_id == UUID("018f47c2-4d4f-4d5b-8c6a-0f3d9ec6bc14")


def test_accepts_canonical_selected_seller_uuid() -> None:
    seller_id = "33333333-3333-4333-8333-333333333333"
    envelope = verify_token(encode(payload(seller_organization_id=seller_id)))
    assert envelope.seller_organization_id == UUID(seller_id)


def test_accepts_public_webhook_endpoint_key_in_path() -> None:
    endpoint_key = "AbCdEFghIjKlMNopQRstUVwxYZ0123456789-_aBc"
    path = f"/v1/webhooks/checkouts/{endpoint_key}"
    token = encode(payload(capability="commerce.webhook.ingest", path=path))

    envelope = verify_token(
        token,
        expected_capability="commerce.webhook.ingest",
        path=path,
    )

    assert envelope.path == path


def test_treats_nbsp_as_exact_identity_data() -> None:
    subject = "\u00a0subject"
    identity = {
        "issuer": IDENTITY_ISSUER,
        "sub": subject,
        "user_id": str(uuid5(NAMESPACE_URL, f"{IDENTITY_ISSUER}\0{subject}")),
    }
    envelope = verify_token(encode(payload(identity=identity)))
    assert envelope.identity.sub == subject


@pytest.mark.parametrize(
    "request_change",
    [
        {"method": "PUT"},
        {"path": "/v1/me"},
        {"query": b"page=2"},
        {"body": b"{}"},
        {"expected_capability": "identity.read"},
    ],
)
def test_request_binding_rejects_substitution(request_change: dict[str, Any]) -> None:
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload()), **request_change)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 2),
        ("version", True),
        ("issuer", "attacker"),
        ("audience", "another-api"),
        ("session_ref", "A" * 64),
        ("session_epoch", 0),
        ("capability", "Seller.List"),
        ("seller_organization_id", "NOT-A-UUID"),
        ("method", "get"),
        ("path", "/v1//me"),
        ("body_sha256", "0" * 63),
        ("request_id", "018F47C2-4D4F-4D5B-8C6A-0F3D9EC6BC14"),
        ("iat", NOW + 6),
        ("exp", NOW + 61),
    ],
)
def test_rejects_invalid_or_substituted_top_level_fields(field: str, value: Any) -> None:
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(**{field: value})))


@pytest.mark.parametrize(
    "identity",
    [
        {"issuer": IDENTITY_ISSUER, "sub": "github|12345"},
        {
            "issuer": "https://attacker.example",
            "sub": "github|12345",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        {
            "issuer": IDENTITY_ISSUER,
            "sub": "bad\nsubject",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        {
            "issuer": IDENTITY_ISSUER,
            "sub": "bad\u0085subject",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        {
            "issuer": IDENTITY_ISSUER,
            "sub": "\ud800ab",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        {
            "issuer": IDENTITY_ISSUER,
            "sub": "\udc00ab",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        {
            "issuer": IDENTITY_ISSUER,
            "sub": " github|12345",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        {
            "issuer": IDENTITY_ISSUER,
            "sub": "ab",
            "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
        },
        {
            "issuer": IDENTITY_ISSUER,
            "sub": "github|12345",
            "user_id": "192098ec0d8a5c689606bcee42ec94d7",
        },
    ],
)
def test_rejects_invalid_identity(identity: dict[str, Any]) -> None:
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(identity=identity)))


def test_rejects_extra_fields_even_with_valid_signature() -> None:
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(admin=True)))


def test_rejects_signature_and_payload_tampering() -> None:
    token = encode(payload())
    prefix, payload_segment, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(ServiceAuthError):
        verify_token(f"{prefix}.{payload_segment}.{tampered_signature}")

    decoded = json.loads(base64.urlsafe_b64decode(payload_segment + "=="))
    decoded["capability"] = "platform.bootstrap"
    tampered_payload = (
        base64.urlsafe_b64encode(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(ServiceAuthError):
        verify_token(f"{prefix}.{tampered_payload}.{signature}")


@pytest.mark.parametrize(
    "token_transform",
    [
        lambda token: token.replace("v1.", "v2.", 1),
        lambda token: token + ".extra",
        lambda token: token.replace(".", ".=", 1),
        lambda token: token[:-1] + "é",
    ],
)
def test_rejects_malformed_token(token_transform: Any) -> None:
    with pytest.raises(ServiceAuthError):
        verify_token(token_transform(encode(payload())))


def test_rejects_noncanonical_base64url_and_oversized_payload() -> None:
    token = encode(payload())
    prefix, payload_segment, signature = token.split(".")
    with pytest.raises(ServiceAuthError):
        verify_token(f"{prefix}.{payload_segment}=.{signature}")

    oversized_identity = {
        "issuer": IDENTITY_ISSUER,
        "sub": "x" * 4096,
        "user_id": "192098ec-0d8a-5c68-9606-bcee42ec94d7",
    }
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(identity=oversized_identity)))


def test_rejects_duplicate_json_keys_and_invalid_utf8() -> None:
    duplicate = b'{"version":1,"version":1}'
    duplicate_segment = base64.urlsafe_b64encode(duplicate).rstrip(b"=").decode()
    duplicate_signature = (
        base64.urlsafe_b64encode(
            hmac.digest(
                SIGNING_KEY,
                f"appsell.internal.v1.{duplicate_segment}".encode(),
                "sha256",
            )
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(ServiceAuthError):
        verify_token(f"v1.{duplicate_segment}.{duplicate_signature}")

    invalid_segment = base64.urlsafe_b64encode(b"\xff").rstrip(b"=").decode()
    invalid_signature = (
        base64.urlsafe_b64encode(
            hmac.digest(
                SIGNING_KEY,
                f"appsell.internal.v1.{invalid_segment}".encode(),
                "sha256",
            )
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(ServiceAuthError):
        verify_token(f"v1.{invalid_segment}.{invalid_signature}")


def test_accepts_only_allowlisted_kid() -> None:
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(kid="unknown")))


@pytest.mark.parametrize(
    "keyring",
    [
        {},
        {KEY_ID: "not-base64!"},
        {KEY_ID: SIGNING_KEY_B64.rstrip("=")},
        {KEY_ID: base64.b64encode(b"short").decode()},
        {"invalid kid": SIGNING_KEY_B64},
    ],
)
def test_rejects_unsafe_keyring_configuration(keyring: dict[str, str]) -> None:
    with pytest.raises(ServiceAuthConfigurationError):
        verifier(keyring=keyring)


def test_rejects_reused_context_hmac_key() -> None:
    with pytest.raises(ServiceAuthConfigurationError):
        verifier(context_hmac_key=SIGNING_KEY_B64)


def test_clock_skew_is_bounded_to_five_seconds() -> None:
    verify_token(encode(payload(iat=NOW + 5, exp=NOW + 6)))
    verify_token(encode(payload(iat=NOW - 35, exp=NOW - 5)))

    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(iat=NOW + 6, exp=NOW + 7)))
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(iat=NOW - 36, exp=NOW - 6)))


def test_expiration_must_be_after_iat_and_within_sixty_seconds() -> None:
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(iat=NOW, exp=NOW)))
    with pytest.raises(ServiceAuthError):
        verify_token(encode(payload(iat=NOW - 1, exp=NOW + 60)))
