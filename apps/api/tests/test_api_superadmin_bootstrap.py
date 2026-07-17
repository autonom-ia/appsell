from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import psycopg
import pytest

from appsell_api.config import get_settings
from appsell_api.main import app

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
BOOTSTRAP_KEY_HEX = os.environ.get("APPSELL_TEST_BOOTSTRAP_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX, BOOTSTRAP_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

INTERNAL_KEY = bytes(range(64, 96))
SERVICE_CREDENTIAL = bytes(range(96, 128))
KEY_ID = "test-v1"
INTERNAL_ISSUER = "appsell-bff-test"
INTERNAL_AUDIENCE = "appsell-api-test"

# The bootstrap trust policy is provisioned by configure_context_key with these
# values (see scripts/test-postgres-foundation.sh). The domain function binds
# the identity user id to app_private.identity_user_id(issuer, sub), so the
# service envelope must present the same identity issuer as the trust policy.
BOOTSTRAP_ISSUER = "https://identity.dev.autonomia.site"
BOOTSTRAP_AUDIENCE = "appsell-web-dev"
BOOTSTRAP_CLIENT_ID = "appsell-web-dev"
BOOTSTRAP_EMAIL = "comercial@autonomia.site"
SESSION_ID = f"api-bootstrap-session-{uuid4()}"


def _derived_user_id(subject: str) -> object:
    return uuid5(NAMESPACE_URL, f"{BOOTSTRAP_ISSUER}\0{subject}")


def _reset_bootstrap_state(*, enabled: bool) -> None:
    """Return the singleton bootstrap to an unconsumed, known-enabled state.

    The consumed check is global (any superadmin grant or bootstrap row blocks),
    so sibling suites that seed a superadmin grant must be cleared here to make
    the first-consume path observable.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        connection.execute("set role appsell_owner")
        connection.execute("delete from superadmin_bootstrap")
        connection.execute("delete from platform_grant where role = 'platform_superadmin'")
        connection.execute(
            "update app_private.bootstrap_trust_policy set enabled = %s where singleton",
            (enabled,),
        )


@pytest.fixture
def bootstrap_ready() -> Iterator[None]:
    _reset_bootstrap_state(enabled=True)
    yield
    _reset_bootstrap_state(enabled=True)


@pytest.fixture(scope="module", autouse=True)
def restore_consumed_lock() -> Iterator[None]:
    """Leave the shared bootstrap in the consumed, locked state on teardown.

    The postgres-foundation CI script asserts, after this suite, that a consumed
    bootstrap can never be re-enabled (``configure_context_key`` must refuse).
    The earlier ``test_postgres_foundation`` suite establishes that consumed,
    disabled state; because this suite toggles the same global singleton per test
    (clearing the grant and re-enabling the policy for isolation), it must restore
    the invariant afterwards instead of leaving the bootstrap re-openable.
    """
    yield
    assert ADMIN_URL
    subject = f"bootstrap-lock-{uuid4()}"
    locked_user_id = _derived_user_id(subject)
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        connection.execute("set role appsell_owner")
        connection.execute("delete from superadmin_bootstrap")
        connection.execute("delete from platform_grant where role = 'platform_superadmin'")
        connection.execute(
            "insert into identity_user "
            "(id, identity_issuer, identity_sub, email_normalized, email_verified) "
            "values (%s, %s, %s, %s, true) on conflict (id) do nothing",
            (locked_user_id, BOOTSTRAP_ISSUER, subject, f"{subject}@example.test"),
        )
        connection.execute(
            "insert into platform_grant (identity_user_id, role, status) "
            "values (%s, 'platform_superadmin', 'active')",
            (locked_user_id,),
        )
        connection.execute(
            "insert into superadmin_bootstrap "
            "(identity_user_id, identity_sub, assurance, request_id) "
            "values (%s, %s, 'mfa', gen_random_uuid())",
            (locked_user_id, subject),
        )
        connection.execute(
            "update app_private.bootstrap_trust_policy set enabled = false where singleton"
        )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    assert RUNTIME_URL and CONTEXT_KEY_HEX
    environment = {
        "APPSELL_API_ENVIRONMENT": "ci",
        "APPSELL_API_DATABASE_URL": RUNTIME_URL.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        ),
        "APPSELL_API_INTERNAL_ISSUER": INTERNAL_ISSUER,
        "APPSELL_API_INTERNAL_AUDIENCE": INTERNAL_AUDIENCE,
        "APPSELL_API_IDENTITY_ISSUER": BOOTSTRAP_ISSUER,
        "APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON": json.dumps(
            [{"id": KEY_ID, "key": base64.b64encode(INTERNAL_KEY).decode(), "active": True}]
        ),
        "APPSELL_API_CONTEXT_HMAC_KEY_B64": base64.b64encode(
            bytes.fromhex(CONTEXT_KEY_HEX)
        ).decode(),
        "APPSELL_API_SERVICE_CREDENTIAL_B64": base64.b64encode(SERVICE_CREDENTIAL).decode(),
    }
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    get_settings.cache_clear()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://api.test"
            ) as test_client:
                yield test_client
    finally:
        get_settings.cache_clear()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _sign_attestation(
    *,
    user_id: object,
    subject: str,
    email: str,
    email_verified: bool,
    assurance: str,
    issuer: str,
    audience: str,
    client_id: str,
    request_id: str,
    issued_at: int,
    expires_at: int,
) -> str:
    def field_hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    payload = ":".join(
        (
            "appsell.bootstrap.v1",
            str(user_id),
            field_hash(subject),
            field_hash(email.strip().lower()),
            str(email_verified).lower(),
            assurance,
            field_hash(issuer),
            field_hash(audience),
            field_hash(client_id),
            request_id,
            str(issued_at),
            str(expires_at),
        )
    )
    assert BOOTSTRAP_KEY_HEX
    return hmac.new(bytes.fromhex(BOOTSTRAP_KEY_HEX), payload.encode(), hashlib.sha256).hexdigest()


def _signed_headers(
    *,
    path: str,
    capability: str,
    body: bytes,
    subject: str,
    user_id: object,
    request_id: str,
    seller_id: str | None = None,
) -> dict[str, str]:
    now = int(time.time())
    payload = {
        "version": 1,
        "kid": KEY_ID,
        "issuer": INTERNAL_ISSUER,
        "audience": INTERNAL_AUDIENCE,
        "session_ref": hashlib.sha256(
            b"appsell.session-ref.v1\n" + SESSION_ID.encode()
        ).hexdigest(),
        "session_epoch": 1,
        "identity": {
            "issuer": BOOTSTRAP_ISSUER,
            "sub": subject,
            "user_id": str(user_id),
        },
        "capability": capability,
        "seller_organization_id": seller_id,
        "method": "POST",
        "path": path,
        "query_sha256": hashlib.sha256(b"").hexdigest(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "request_id": request_id,
        "iat": now,
        "exp": now + 30,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(
        b"="
    )
    signature = base64.urlsafe_b64encode(
        hmac.digest(INTERNAL_KEY, b"appsell.internal.v1." + encoded, "sha256")
    ).rstrip(b"=")
    return {
        "x-appsell-service-credential": base64.b64encode(SERVICE_CREDENTIAL).decode(),
        "x-appsell-internal-envelope": f"v1.{encoded.decode()}.{signature.decode()}",
        "content-type": "application/json",
    }


PATH = "/v1/platform/bootstrap-superadmin"


def _bootstrap_request(
    *,
    subject: str,
    email: str = BOOTSTRAP_EMAIL,
    email_verified: bool = True,
    assurance: str = "mfa",
    issuer: str = BOOTSTRAP_ISSUER,
    audience: str = BOOTSTRAP_AUDIENCE,
    client_id: str = BOOTSTRAP_CLIENT_ID,
) -> tuple[bytes, dict[str, str]]:
    user_id = _derived_user_id(subject)
    request_id = str(uuid4())
    issued_at = int(time.time())
    expires_at = issued_at + 60
    signature = _sign_attestation(
        user_id=user_id,
        subject=subject,
        email=email,
        email_verified=email_verified,
        assurance=assurance,
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        request_id=request_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    body = json.dumps(
        {
            "email": email,
            "email_verified": email_verified,
            "assurance": assurance,
            "issuer": issuer,
            "audience": audience,
            "client_id": client_id,
            "attestation_issued_at": issued_at,
            "attestation_expires_at": expires_at,
            "attestation_signature": signature,
            "source_ip_hash": "a" * 64,
        },
        separators=(",", ":"),
    ).encode()
    headers = _signed_headers(
        path=PATH,
        capability="platform.bootstrap",
        body=body,
        subject=subject,
        user_id=user_id,
        request_id=request_id,
    )
    return body, headers


@pytest.mark.asyncio
async def test_first_consume_grants_superadmin(
    client: httpx.AsyncClient, bootstrap_ready: None
) -> None:
    subject = f"bootstrap-ok-{uuid4()}"
    body, headers = _bootstrap_request(subject=subject)

    response = await client.post(PATH, content=body, headers=headers)

    assert response.status_code == 201
    assert response.json() == {"id": str(_derived_user_id(subject))}
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        grant = connection.execute(
            "select count(*) from platform_grant "
            "where identity_user_id = %s and role = 'platform_superadmin' and status = 'active'",
            (_derived_user_id(subject),),
        ).fetchone()
        consumed = connection.execute(
            "select count(*) from superadmin_bootstrap where identity_user_id = %s",
            (_derived_user_id(subject),),
        ).fetchone()
        disabled = connection.execute(
            "select enabled from app_private.bootstrap_trust_policy where singleton"
        ).fetchone()
    assert grant == (1,)
    assert consumed == (1,)
    assert disabled == (False,)


@pytest.mark.asyncio
async def test_second_consume_is_rejected(client: httpx.AsyncClient, bootstrap_ready: None) -> None:
    """A successful consume disables the trust policy, so the natural second
    attempt is rejected fail-closed by the disabled-policy gate (403). The
    conflict path (409) requires an operator to re-enable the policy and is
    covered by test_reenabled_policy_after_consumption_conflicts."""
    first_body, first_headers = _bootstrap_request(subject=f"bootstrap-first-{uuid4()}")
    first = await client.post(PATH, content=first_body, headers=first_headers)
    assert first.status_code == 201

    second_subject = f"bootstrap-second-{uuid4()}"
    second_body, second_headers = _bootstrap_request(subject=second_subject)
    second = await client.post(PATH, content=second_body, headers=second_headers)

    assert second.status_code == 403
    assert second.json() == {"detail": "forbidden"}
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select count(*) from platform_grant "
            "where role = 'platform_superadmin' and status = 'active'"
        ).fetchone() == (1,)
        assert connection.execute(
            "select count(*) from superadmin_bootstrap where identity_user_id = %s",
            (_derived_user_id(second_subject),),
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_reenabled_policy_after_consumption_conflicts(
    client: httpx.AsyncClient, bootstrap_ready: None
) -> None:
    """If an operator re-enables the trust policy while a superadmin grant is
    active, the single-consumption invariant still holds and surfaces as 409."""
    first_body, first_headers = _bootstrap_request(subject=f"bootstrap-consumed-{uuid4()}")
    first = await client.post(PATH, content=first_body, headers=first_headers)
    assert first.status_code == 201

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        connection.execute("set role appsell_owner")
        connection.execute(
            "update app_private.bootstrap_trust_policy set enabled = true where singleton"
        )

    retry_subject = f"bootstrap-retry-{uuid4()}"
    retry_body, retry_headers = _bootstrap_request(subject=retry_subject)
    retry = await client.post(PATH, content=retry_body, headers=retry_headers)

    assert retry.status_code == 409
    assert retry.json() == {"detail": "resource_conflict"}
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select count(*) from superadmin_bootstrap where identity_user_id = %s",
            (_derived_user_id(retry_subject),),
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_insufficient_claims_are_rejected(
    client: httpx.AsyncClient, bootstrap_ready: None
) -> None:
    subject = f"bootstrap-weak-{uuid4()}"
    body, headers = _bootstrap_request(subject=subject, assurance="password")

    response = await client.post(PATH, content=body, headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select count(*) from superadmin_bootstrap where identity_user_id = %s",
            (_derived_user_id(subject),),
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_disabled_bootstrap_config_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    _reset_bootstrap_state(enabled=False)
    try:
        subject = f"bootstrap-disabled-{uuid4()}"
        body, headers = _bootstrap_request(subject=subject)

        response = await client.post(PATH, content=body, headers=headers)

        assert response.status_code == 403
        assert response.json() == {"detail": "forbidden"}
        assert ADMIN_URL
        with psycopg.connect(ADMIN_URL) as connection:
            assert connection.execute("select count(*) from superadmin_bootstrap").fetchone() == (
                0,
            )
    finally:
        _reset_bootstrap_state(enabled=True)


@pytest.mark.asyncio
async def test_invalid_body_is_rejected(client: httpx.AsyncClient, bootstrap_ready: None) -> None:
    invalid = json.dumps({"email": BOOTSTRAP_EMAIL}, separators=(",", ":")).encode()
    subject = f"bootstrap-invalid-{uuid4()}"
    headers = _signed_headers(
        path=PATH,
        capability="platform.bootstrap",
        body=invalid,
        subject=subject,
        user_id=_derived_user_id(subject),
        request_id=str(uuid4()),
    )

    response = await client.post(PATH, content=invalid, headers=headers)

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request_body"}
