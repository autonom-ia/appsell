from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from appsell_api.config import get_settings
from appsell_api.main import app
from appsell_api.service_auth import ServiceEnvelope, ServiceIdentity

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

INTERNAL_KEY = bytes(range(64, 96))
SERVICE_CREDENTIAL = bytes(range(96, 128))
KEY_ID = "test-v1"
INTERNAL_ISSUER = "appsell-bff-test"
INTERNAL_AUDIENCE = "appsell-api-test"
IDENTITY_ISSUER = "https://identity.test.autonomia.site"
SUBJECT = f"api-user-{uuid4()}"
IDENTITY_ID = uuid5(NAMESPACE_URL, f"{IDENTITY_ISSUER}\0{SUBJECT}")
SESSION_ID = f"api-session-{uuid4()}"


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
        "APPSELL_API_IDENTITY_ISSUER": IDENTITY_ISSUER,
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


def signed_headers(
    *,
    method: str,
    path: str,
    capability: str,
    body: bytes = b"",
    query: bytes = b"",
    request_id: str | None = None,
    seller_id: str | None = None,
    issued_at: int | None = None,
    expires_at: int | None = None,
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
            "issuer": IDENTITY_ISSUER,
            "sub": SUBJECT,
            "user_id": str(IDENTITY_ID),
        },
        "capability": capability,
        "seller_organization_id": seller_id,
        "method": method,
        "path": path,
        "query_sha256": hashlib.sha256(query).hexdigest(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "request_id": request_id or str(uuid4()),
        "iat": issued_at if issued_at is not None else now,
        "exp": expires_at if expires_at is not None else now + 30,
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


@pytest.mark.asyncio
async def test_identity_onboarding_and_seller_listing_are_persistent(
    client: httpx.AsyncClient,
) -> None:
    sync_body = json.dumps(
        {"email": "creator@example.test", "email_verified": True}, separators=(",", ":")
    ).encode()
    synced = await client.put(
        "/v1/me",
        content=sync_body,
        headers=signed_headers(
            method="PUT", path="/v1/me", capability="identity.sync", body=sync_body
        ),
    )
    assert synced.status_code == 200
    assert synced.json() == {
        "id": str(IDENTITY_ID),
        "email": "creator@example.test",
        "email_verified": True,
        "display_name": None,
    }

    me = await client.get(
        "/v1/me",
        headers=signed_headers(method="GET", path="/v1/me", capability="platform.grants.read"),
    )
    assert me.status_code == 200
    assert me.json()["platform_roles"] == []
    assert "identity_sub" not in me.text

    slug = f"seller-{uuid4().hex[:12]}"
    onboard_body = json.dumps(
        {
            "idempotency_key": str(uuid4()),
            "slug": slug,
            "display_name": "Creator Persistente",
        },
        separators=(",", ":"),
    ).encode()
    onboarded = await client.post(
        "/v1/me/sellers",
        content=onboard_body,
        headers=signed_headers(
            method="POST",
            path="/v1/me/sellers",
            capability="seller.onboard",
            body=onboard_body,
        ),
    )
    assert onboarded.status_code == 201

    sellers = await client.get(
        "/v1/me/sellers",
        headers=signed_headers(method="GET", path="/v1/me/sellers", capability="seller.list"),
    )
    assert sellers.status_code == 200
    assert sellers.json() == {
        "sellers": [
            {
                "id": onboarded.json()["id"],
                "slug": slug,
                "display_name": "Creator Persistente",
                "role": "seller_owner",
            }
        ]
    }


@pytest.mark.asyncio
async def test_replay_and_request_substitution_fail_closed(client: httpx.AsyncClient) -> None:
    request_id = str(uuid4())
    headers = signed_headers(
        method="GET",
        path="/v1/me/sellers",
        capability="seller.list",
        request_id=request_id,
    )
    assert (await client.get("/v1/me/sellers", headers=headers)).status_code == 200
    assert (await client.get("/v1/me/sellers", headers=headers)).status_code == 401

    valid = signed_headers(method="GET", path="/v1/me/sellers", capability="seller.list")
    assert (await client.get("/v1/me?changed=1", headers=valid)).status_code == 401
    assert (
        await client.get(
            "/v1/me/sellers",
            headers={**valid, "x-appsell-service-credential": base64.b64encode(b"x" * 32).decode()},
        )
    ).status_code == 401

    expired_now = int(time.time())
    expired_in_clock_skew = signed_headers(
        method="GET",
        path="/v1/me/sellers",
        capability="seller.list",
        issued_at=expired_now - 2,
        expires_at=expired_now - 1,
    )
    expired = await client.get("/v1/me/sellers", headers=expired_in_clock_skew)
    assert expired.status_code == 401
    assert expired.json() == {"detail": "invalid_internal_request"}


@pytest.mark.asyncio
async def test_domain_rejection_rolls_back_business_work_but_consumes_replay(
    client: httpx.AsyncClient,
) -> None:
    seller_id = str(uuid4())
    path = f"/v1/me/sellers/{seller_id}/products"
    body = json.dumps(
        {
            "idempotency_key": str(uuid4()),
            "slug": "replay-rejected-product",
            "title": "Replay Rejected Product",
            "description": "Must roll back",
            "course_title": "Replay Rejected Course",
        },
        separators=(",", ":"),
    ).encode()
    rejected_headers = signed_headers(
        method="POST",
        path=path,
        capability="catalog.draft.create",
        body=body,
        seller_id=seller_id,
    )
    rejected = await client.post(path, content=body, headers=rejected_headers)
    assert rejected.status_code == 403
    assert rejected.json() == {"detail": "forbidden"}
    replayed = await client.post(path, content=body, headers=rejected_headers)
    assert replayed.status_code == 401
    assert replayed.json() == {"detail": "invalid_internal_request"}


@pytest.mark.asyncio
async def test_duplicate_or_unsigned_business_fields_are_rejected(
    client: httpx.AsyncClient,
) -> None:
    duplicate = (
        b'{"idempotency_key":"'
        + str(uuid4()).encode()
        + b'","slug":"seller-one","slug":"seller-two","display_name":"Duplicado"}'
    )
    duplicate_headers = signed_headers(
        method="POST",
        path="/v1/me/sellers",
        capability="seller.onboard",
        body=duplicate,
    )
    response = await client.post(
        "/v1/me/sellers",
        content=duplicate,
        headers=duplicate_headers,
    )
    assert response.status_code == 422
    assert "seller-one" not in response.text
    assert "seller-two" not in response.text
    replayed = await client.post("/v1/me/sellers", content=duplicate, headers=duplicate_headers)
    assert replayed.status_code == 401
    assert replayed.json() == {"detail": "invalid_internal_request"}


@pytest.mark.asyncio
async def test_deferred_constraint_failure_preserves_replay_guard(
    client: httpx.AsyncClient,
) -> None:
    assert ADMIN_URL
    table_name = f"deferred_replay_probe_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(ADMIN_URL) as admin:
        await admin.execute(
            f'create table public."{table_name}" ('
            "id integer primary key, parent_id integer, "
            f'constraint "{table_name}_fk" foreign key(parent_id) '
            f'references public."{table_name}"(id) deferrable initially deferred)'
        )
        await admin.execute(f'grant insert on public."{table_name}" to appsell_runtime')
        await admin.commit()

    now = int(time.time())
    envelope = ServiceEnvelope(
        version=1,
        kid=KEY_ID,
        issuer=INTERNAL_ISSUER,
        audience=INTERNAL_AUDIENCE,
        session_ref=hashlib.sha256(b"deferred-replay-probe").hexdigest(),
        session_epoch=1,
        identity=ServiceIdentity(
            issuer=IDENTITY_ISSUER,
            sub=SUBJECT,
            user_id=IDENTITY_ID,
        ),
        capability="catalog.draft.create",
        seller_organization_id=None,
        method="POST",
        path="/v1/deferred-replay-probe",
        query_sha256=hashlib.sha256(b"").hexdigest(),
        body_sha256=hashlib.sha256(b"").hexdigest(),
        request_id=uuid4(),
        iat=now,
        exp=now + 30,
    )
    auth = app.state.internal_auth
    assert auth is not None
    try:
        with pytest.raises(DBAPIError):
            async with auth.domain_session(envelope) as session:
                await session.execute(
                    text(f'insert into public."{table_name}"(id,parent_id) values(1,2)')
                )

        with pytest.raises(HTTPException) as replayed:
            async with auth.domain_session(envelope):
                pytest.fail("deferred constraint failure did not consume replay")
        assert replayed.value.status_code == 401
        assert replayed.value.detail == "invalid_internal_request"
    finally:
        async with await psycopg.AsyncConnection.connect(ADMIN_URL) as admin:
            await admin.execute(f'drop table if exists public."{table_name}"')
            await admin.commit()
