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

from appsell_api.config import get_settings
from appsell_api.main import app
from appsell_api.reset_demo_tenant import load_manifest, reset_demo

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
SUPERADMIN_SUB = "api-inspection-superadmin"
SUPERADMIN_ID = uuid5(NAMESPACE_URL, f"{IDENTITY_ISSUER}\0{SUPERADMIN_SUB}")
UNAUTHORIZED_SUB = "api-inspection-unauthorized"
UNAUTHORIZED_ID = uuid5(NAMESPACE_URL, f"{IDENTITY_ISSUER}\0{UNAUTHORIZED_SUB}")
SESSION_ID = f"api-inspection-session-{uuid4()}"


@pytest.fixture(scope="module", autouse=True)
def seed_demo_and_grant() -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, IDENTITY_ISSUER)
        connection.execute(
            "insert into identity_user("
            "id,identity_issuer,identity_sub,email_normalized,email_verified) "
            "values(%s,%s,%s,'api.inspection.superadmin@example.test',true),"
            "(%s,%s,%s,'api.inspection.unauthorized@example.test',true) "
            "on conflict(id) do nothing",
            (
                SUPERADMIN_ID,
                IDENTITY_ISSUER,
                SUPERADMIN_SUB,
                UNAUTHORIZED_ID,
                IDENTITY_ISSUER,
                UNAUTHORIZED_SUB,
            ),
        )
        connection.execute(
            "insert into platform_grant(identity_user_id,role,status) "
            "values(%s,'platform_superadmin','active') "
            "on conflict(identity_user_id,role) do update set status='active',revoked_at=null",
            (SUPERADMIN_ID,),
        )
        connection.commit()


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
    subject: str = SUPERADMIN_SUB,
    user_id: object = SUPERADMIN_ID,
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
            "issuer": IDENTITY_ISSUER,
            "sub": subject,
            "user_id": str(user_id),
        },
        "capability": capability,
        "seller_organization_id": seller_id,
        "method": method,
        "path": path,
        "query_sha256": hashlib.sha256(b"").hexdigest(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "request_id": str(uuid4()),
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


@pytest.mark.asyncio
async def test_api_starts_reads_and_revokes_only_the_server_demo(client: httpx.AsyncClient) -> None:
    manifest, _ = load_manifest()
    start_body = json.dumps(
        {"reason": "Demonstracao assistida para cliente", "ttl_seconds": 300},
        separators=(",", ":"),
    ).encode()
    started = await client.post(
        "/v1/platform/demo-inspections",
        content=start_body,
        headers=signed_headers(
            method="POST",
            path="/v1/platform/demo-inspections",
            capability="platform.demo.inspect.start",
            body=start_body,
        ),
    )
    assert started.status_code == 201
    started_payload = started.json()
    inspection_id = started_payload["inspection_id"]
    token = started_payload["inspection_token"]
    assert len(token) == 43 and "=" not in token
    assert started_payload["seller"] == {
        "id": manifest["seller"]["id"],
        "slug": manifest["seller"]["slug"],
        "display_name": manifest["seller"]["display_name"],
    }

    path = f"/v1/platform/demo-inspections/{inspection_id}"
    read = await client.get(
        path,
        headers={
            **signed_headers(
                method="GET", path=path, capability="platform.demo.inspect.read"
            ),
            "x-appsell-inspection-token": token,
        },
    )
    assert read.status_code == 200
    assert read.json()["seller"]["id"] == manifest["seller"]["id"]
    assert read.json()["product"]["id"] == manifest["product"]["id"]
    assert "email" not in read.text
    assert "buyer" not in read.text
    assert "reason" not in read.text

    revoke_path = f"{path}/revoke"
    revoke_body = b'{"reason":"Atendimento demonstrativo encerrado"}'
    revoked = await client.post(
        revoke_path,
        content=revoke_body,
        headers={
            **signed_headers(
                method="POST",
                path=revoke_path,
                capability="platform.demo.inspect.revoke",
                body=revoke_body,
            ),
            "x-appsell-inspection-token": token,
        },
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    after_revoke = await client.get(
        path,
        headers={
            **signed_headers(
                method="GET", path=path, capability="platform.demo.inspect.read"
            ),
            "x-appsell-inspection-token": token,
        },
    )
    assert after_revoke.status_code == 404
    assert after_revoke.json() == {"detail": "inspection_not_found"}


@pytest.mark.asyncio
async def test_api_rejects_missing_token_unauthorized_identity_and_tenant_input(
    client: httpx.AsyncClient,
) -> None:
    invalid_body = b'{"reason":"Motivo valido para inspecao","ttl_seconds":300,"seller_id":"x"}'
    invalid = await client.post(
        "/v1/platform/demo-inspections",
        content=invalid_body,
        headers=signed_headers(
            method="POST",
            path="/v1/platform/demo-inspections",
            capability="platform.demo.inspect.start",
            body=invalid_body,
        ),
    )
    assert invalid.status_code == 422

    ordinary_body = b'{"reason":"Motivo valido para inspecao","ttl_seconds":300}'
    ordinary = await client.post(
        "/v1/platform/demo-inspections",
        content=ordinary_body,
        headers=signed_headers(
            method="POST",
            path="/v1/platform/demo-inspections",
            capability="platform.demo.inspect.start",
            body=ordinary_body,
            subject=UNAUTHORIZED_SUB,
            user_id=UNAUTHORIZED_ID,
        ),
    )
    assert ordinary.status_code == 403

    missing_path = f"/v1/platform/demo-inspections/{uuid4()}"
    missing = await client.get(
        missing_path,
        headers=signed_headers(
            method="GET", path=missing_path, capability="platform.demo.inspect.read"
        ),
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_api_hides_inspection_routes_outside_local_ci(
    client: httpx.AsyncClient,
) -> None:
    schema = await client.get("/openapi.json")
    assert schema.status_code == 404

    original_environment = os.environ["APPSELL_API_ENVIRONMENT"]
    original_database_url = os.environ["APPSELL_API_DATABASE_URL"]
    try:
        for environment in ("staging", "production"):
            os.environ["APPSELL_API_ENVIRONMENT"] = environment
            if environment == "production":
                os.environ["APPSELL_API_DATABASE_URL"] = (
                    "postgresql+asyncpg://appsell_runtime:unused@db.example.test/appsell"
                    "?ssl=verify-full"
                )
            get_settings.cache_clear()
            response = await client.post(
                "/v1/platform/demo-inspections",
                json={"reason": "Route must remain unavailable", "ttl_seconds": 300},
            )
            assert response.status_code == 404
            assert response.json() == {"detail": "not_found"}
    finally:
        os.environ["APPSELL_API_ENVIRONMENT"] = original_environment
        os.environ["APPSELL_API_DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_existing_onboarding_cannot_create_superadmin_membership(
    client: httpx.AsyncClient,
) -> None:
    body = json.dumps(
        {
            "idempotency_key": str(uuid4()),
            "slug": f"forbidden-{uuid4().hex[:8]}",
            "display_name": "Tenant proibida",
        },
        separators=(",", ":"),
    ).encode()
    response = await client.post(
        "/v1/me/sellers",
        content=body,
        headers=signed_headers(
            method="POST", path="/v1/me/sellers", capability="seller.onboard", body=body
        ),
    )
    assert response.status_code == 403
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select count(*) from seller_membership where identity_user_id=%s",
            (SUPERADMIN_ID,),
        ).fetchone() == (0,)
