from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import time
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import psycopg
import pytest
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api import main as main_module
from appsell_api.config import Settings, get_settings
from appsell_api.main import app
from appsell_api.reset_demo_tenant import (
    ASSET_WORKER_SUBJECT,
    load_manifest,
    reset_demo,
)
from appsell_api.storage_s3 import S3Storage
from appsell_api.storage_worker import DeterministicLocalScanner, PdfStorageWorker, VerifyPdfJob

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
ENDPOINT = os.environ.get("APPSELL_TEST_S3_ENDPOINT")
ACCESS_KEY = os.environ.get("APPSELL_TEST_S3_ACCESS_KEY")
SECRET_KEY = os.environ.get("APPSELL_TEST_S3_SECRET_KEY")
ISSUER = "https://identity.storage-vertical.test.autonomia.site"
INTERNAL_ISSUER = "appsell-storage-bff-test"
INTERNAL_AUDIENCE = "appsell-storage-api-test"
INTERNAL_KEY = bytes(range(64, 96))
SERVICE_CREDENTIAL = bytes(range(96, 128))
KEY_ID = "storage-test-v1"

pytestmark = pytest.mark.skipif(
    not all(
        (
            ADMIN_URL,
            RUNTIME_URL,
            CONTEXT_KEY_HEX,
            ENDPOINT,
            ACCESS_KEY,
            SECRET_KEY,
        )
    ),
    reason="isolated PostgreSQL and real S3-compatible storage are required",
)


def signed_headers(
    *,
    method: str,
    path: str,
    capability: str,
    subject: str,
    user_id: UUID,
    seller_id: UUID,
    body: bytes,
) -> dict[str, str]:
    now = int(time.time())
    payload = {
        "version": 1,
        "kid": KEY_ID,
        "issuer": INTERNAL_ISSUER,
        "audience": INTERNAL_AUDIENCE,
        "session_ref": hashlib.sha256(b"storage-route-real-session").hexdigest(),
        "session_epoch": 1,
        "identity": {"issuer": ISSUER, "sub": subject, "user_id": str(user_id)},
        "capability": capability,
        "seller_organization_id": str(seller_id),
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


async def create_and_confirm_via_routes(
    *,
    monkeypatch: pytest.MonkeyPatch,
    admin_subject: str,
    admin: UUID,
    seller: UUID,
    product: UUID,
    course: UUID,
    module: UUID,
    lesson: UUID,
    source_pdf: bytes,
    source_sha: str,
) -> UUID:
    environment = {
        "APPSELL_API_ENVIRONMENT": "ci",
        "APPSELL_API_DATABASE_URL": str(RUNTIME_URL).replace(
            "postgresql://", "postgresql+asyncpg://", 1
        ),
        "APPSELL_API_INTERNAL_ISSUER": INTERNAL_ISSUER,
        "APPSELL_API_INTERNAL_AUDIENCE": INTERNAL_AUDIENCE,
        "APPSELL_API_IDENTITY_ISSUER": ISSUER,
        "APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON": json.dumps(
            [
                {
                    "id": KEY_ID,
                    "key": base64.b64encode(INTERNAL_KEY).decode(),
                    "active": True,
                }
            ]
        ),
        "APPSELL_API_CONTEXT_HMAC_KEY_B64": base64.b64encode(
            bytes.fromhex(str(CONTEXT_KEY_HEX))
        ).decode(),
        "APPSELL_API_SERVICE_CREDENTIAL_B64": base64.b64encode(SERVICE_CREDENTIAL).decode(),
        "APPSELL_API_STORAGE_PROVIDER": "s3",
        "APPSELL_API_STORAGE_INTERNAL_ENDPOINT": str(ENDPOINT),
        "APPSELL_API_STORAGE_PUBLIC_ENDPOINT": str(ENDPOINT),
        "APPSELL_API_STORAGE_QUARANTINE_BUCKET": "appsell-quarantine",
        "APPSELL_API_STORAGE_READY_BUCKET": "appsell-ready",
        "APPSELL_API_STORAGE_CONTROL_ACCESS_KEY": str(ACCESS_KEY),
        "APPSELL_API_STORAGE_CONTROL_SECRET_KEY": str(SECRET_KEY),
        "APPSELL_API_STORAGE_ALLOWED_ORIGIN": "http://localhost:3000",
        "APPSELL_API_MALWARE_SCANNER_PROVIDER": "deterministic",
        "APPSELL_API_PDF_SANDBOX_URL": "http://pdf-sandbox.internal:8081/normalize",
    }
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    get_settings.cache_clear()

    async def parser_ready(_settings: Settings) -> bool:
        return True

    async def idle_worker(_app: object, stop: asyncio.Event) -> None:
        await stop.wait()

    monkeypatch.setattr(main_module, "_pdf_sandbox_ready", parser_ready)
    monkeypatch.setattr(main_module, "run_asset_worker_loop", idle_worker)
    try:
        path = (
            f"/v1/me/sellers/{seller}/products/{product}/courses/{course}"
            f"/modules/{module}/lessons/{lesson}/assets/upload-intents"
        )
        create_body = json.dumps(
            {
                "idempotency_key": str(uuid4()),
                "expected_course_version": 1,
                "title": "PDF route real",
                "file_name": "route-real.pdf",
                "media_type": "application/pdf",
                "byte_size": len(source_pdf),
                "sha256": source_sha,
            },
            separators=(",", ":"),
        ).encode()
        async with app.router.lifespan_context(app):
            app.state.inline_asset_worker_enabled = False
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://api.test"
            ) as client:
                created = await client.post(
                    path,
                    content=create_body,
                    headers=signed_headers(
                        method="POST",
                        path=path,
                        capability="catalog.asset.create",
                        subject=admin_subject,
                        user_id=admin,
                        seller_id=seller,
                        body=create_body,
                    ),
                )
                assert created.status_code == 201, created.text
                payload = created.json()
                async with httpx.AsyncClient() as storage_http:
                    uploaded = await storage_http.put(
                        payload["upload_url"],
                        content=source_pdf,
                        headers=payload["required_headers"],
                    )
                assert uploaded.status_code in {200, 201}, uploaded.text

                upload_id = UUID(payload["upload_id"])
                confirm_path = (
                    path.removesuffix("/upload-intents") + f"/uploads/{upload_id}/confirm"
                )
                confirm_body = json.dumps(
                    {"idempotency_key": str(uuid4())}, separators=(",", ":")
                ).encode()
                confirmed = await client.post(
                    confirm_path,
                    content=confirm_body,
                    headers=signed_headers(
                        method="POST",
                        path=confirm_path,
                        capability="catalog.asset.upload.confirm",
                        subject=admin_subject,
                        user_id=admin,
                        seller_id=seller,
                        body=confirm_body,
                    ),
                )
                assert confirmed.status_code == 202, confirmed.text
                assert confirmed.json()["status"] == "uploaded"
                return upload_id
    finally:
        get_settings.cache_clear()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.asyncio
async def test_new_pdf_upload_worker_publication_and_entitled_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, manifest_sha = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    offer = UUID(manifest["offer"]["id"])
    admin_subject = manifest["personas"]["admin"]["subject"]
    admin = identity_id(admin_subject)
    worker = identity_id(ASSET_WORKER_SUBJECT)
    buyer = uuid4()
    entitlement = uuid4()
    outsider = uuid4()

    with psycopg.connect(str(ADMIN_URL)) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        for identity, label in ((buyer, "buyer"), (outsider, "outsider")):
            connection.execute(
                """
                insert into identity_user(
                  id,identity_issuer,identity_sub,email_normalized,email_verified
                ) values(%s,%s,%s,%s,true)
                """,
                (
                    identity,
                    ISSUER,
                    f"{label}-{identity}",
                    f"{label}-{identity}@example.test",
                ),
            )
        connection.execute(
            """
            insert into app_private.asset_worker_identity(identity_user_id,status,label)
            values(%s,'active','Storage vertical worker')
            on conflict(identity_user_id) do update set status='active'
            """,
            (worker,),
        )
        connection.commit()

    storage = storage_client()
    storage.ensure_local_ci_controls(allowed_origin="http://localhost:3000")
    source_pdf = valid_pdf()
    source_sha = hashlib.sha256(source_pdf).hexdigest()
    upload_id = await create_and_confirm_via_routes(
        monkeypatch=monkeypatch,
        admin_subject=admin_subject,
        admin=admin,
        seller=seller,
        product=product,
        course=course,
        module=module,
        lesson=lesson,
        source_pdf=source_pdf,
        source_sha=source_sha,
    )

    with psycopg.connect(str(RUNTIME_URL)) as connection:
        apply_context(connection, worker, None, "catalog.asset.verify")
        lease_row = connection.execute(
            "select app_private.begin_asset_verification(%s,%s,%s,'storage-vertical',120)",
            (worker, seller, upload_id),
        ).fetchone()
        assert lease_row is not None
        lease = lease_row[0]
        connection.commit()

    processed = PdfStorageWorker(
        storage, DeterministicLocalScanner(), isolated_service_boundary=True
    ).process(
        VerifyPdfJob(
            seller_id=seller,
            upload_id=upload_id,
            source_bucket=lease["quarantine_bucket"],
            ready_bucket=lease["ready_bucket"],
            source_key=lease["quarantine_key"],
            ready_key=lease["ready_key"],
            source_version_id=lease["source_version_id"],
            expected_size=lease["expected_byte_size"],
            expected_sha256_hex=lease["expected_sha256"],
            declared_content_type=lease["expected_media_type"],
            file_name=lease["file_name"],
        )
    )

    with psycopg.connect(str(RUNTIME_URL)) as connection:
        apply_context(connection, worker, None, "catalog.asset.verify")
        connection.execute(
            """
            select app_private.register_asset_ready_candidate(
              %s,%s,%s,'storage-vertical',%s,%s,%s,%s,
              'application/pdf','application/pdf','clean')
            """,
            (
                worker,
                seller,
                upload_id,
                processed.ready.version_id,
                processed.ready.etag,
                processed.ready_byte_size,
                processed.ready_sha256_hex,
            ),
        )
        connection.commit()

        apply_context(connection, worker, None, "catalog.asset.verify")
        finalized_row = connection.execute(
            """
            select app_private.finalize_asset_verification(
              %s,%s,%s,'storage-vertical',%s,%s,%s,%s,
              'application/pdf','application/pdf','clean',%s,%s,'ready',%s,%s,%s
            )
            """,
            (
                worker,
                seller,
                upload_id,
                processed.source_byte_size,
                processed.source_sha256_hex,
                processed.ready_byte_size,
                processed.ready_sha256_hex,
                processed.ready.version_id,
                processed.ready.etag,
                uuid4(),
                hashlib.sha256(b"finalize vertical").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
        assert finalized_row is not None
        finalized = finalized_row[0]
        connection.commit()
        assert finalized["status"] == "ready" and finalized["course_version"] == 2
        asset_id = UUID(finalized["asset_id"])

        apply_context(connection, admin, seller, "catalog.publication.create")
        publication = connection.execute(
            "select * from app_private.publish_product(%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
            (
                admin,
                seller,
                product,
                offer,
                json.dumps({str(course): 2}),
                uuid4(),
                hashlib.sha256(b"publish vertical").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
        assert publication is not None and publication[4] is True

    publication_id = publication[0]
    with psycopg.connect(str(ADMIN_URL)) as connection:
        # Controlled test grant: no billing claim is made or simulated.
        connection.execute(
            """
            insert into buyer_entitlement(
              seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
              publication_id,reference_key,source,status
            ) values(%s,%s,%s,%s,%s,%s,'manual','active')
            """,
            (seller, entitlement, buyer, product, publication_id, f"storage-ci-{entitlement}"),
        )
        assert connection.execute(
            "select count(*) from publication_asset where publication_id=%s and asset_id=%s",
            (publication_id, asset_id),
        ).fetchone() == (1,)
        connection.commit()

    with psycopg.connect(str(RUNTIME_URL)) as connection:
        apply_context(connection, buyer, None, "library.asset.read")
        authorized = connection.execute(
            "select * from app_private.authorize_publication_asset(%s,%s,%s)",
            (buyer, entitlement, asset_id),
        ).fetchone()
        connection.commit()
        assert authorized is not None

        apply_context(connection, outsider, None, "library.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_publication_asset(%s,%s,%s)",
                (outsider, entitlement, asset_id),
            ).fetchone()
        connection.rollback()

    assert authorized[7] == "s3"
    assert authorized[8] == "appsell-ready"
    assert authorized[9].startswith(f"tenants/{seller}/assets/")
    assert authorized[5] == processed.ready_byte_size
    assert authorized[6] == processed.ready_sha256_hex
    signed = storage.presign_get(
        bucket=authorized[8],
        key=authorized[9],
        version_id=authorized[10],
        file_name=authorized[2],
    )
    async with httpx.AsyncClient() as storage_http:
        downloaded = await storage_http.get(signed.url, timeout=10)
    assert downloaded.status_code == 200
    assert len(downloaded.content) == authorized[5]
    assert hashlib.sha256(downloaded.content).hexdigest() == authorized[6]
    assert downloaded.headers["cache-control"] == "private, no-store, max-age=0"
    assert downloaded.headers["content-disposition"] == ("inline; filename*=UTF-8''route-real.pdf")


def identity_id(subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{ISSUER}\0{subject}")


def apply_context(
    connection: psycopg.Connection[object], actor: UUID, seller: UUID | None, capability: str
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(
        bytes.fromhex(str(CONTEXT_KEY_HEX)), payload.encode(), hashlib.sha256
    ).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


def storage_client() -> S3Storage:
    return S3Storage(
        Settings(
            environment="ci",
            storage_provider="s3",
            storage_internal_endpoint=str(ENDPOINT),
            storage_public_endpoint=str(ENDPOINT),
            storage_quarantine_bucket="appsell-quarantine",
            storage_ready_bucket="appsell-ready",
            storage_control_access_key=str(ACCESS_KEY),
            storage_control_secret_key=str(SECRET_KEY),
            storage_allowed_origin="http://localhost:3000",
            malware_scanner_provider="deterministic",
            pdf_sandbox_url="http://pdf-sandbox.internal:8081/normalize",
        )
    )


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    canvas.drawString(32, 430, "New upload storage vertical")
    canvas.showPage()
    canvas.save()
    return output.getvalue()
