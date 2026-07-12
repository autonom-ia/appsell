from __future__ import annotations

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

from appsell_api.config import Settings
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


def test_new_pdf_upload_worker_publication_and_entitled_download() -> None:
    manifest, manifest_sha = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    offer = UUID(manifest["offer"]["id"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
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
    upload_id: UUID

    with psycopg.connect(str(RUNTIME_URL)) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        created_row = connection.execute(
            """
            select app_private.create_asset_upload_intent(
              %s,%s,%s,%s,%s,%s,null,1,'PDF vertical real','vertical-real.pdf',
              'application/pdf',%s,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s
            )
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                len(source_pdf),
                source_sha,
                uuid4(),
                hashlib.sha256(b"create vertical").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
        assert created_row is not None
        created = created_row[0]
        connection.commit()
        upload_id = UUID(created["upload_id"])

        apply_context(connection, admin, seller, "catalog.asset.upload.sign")
        signing_row = connection.execute(
            "select app_private.read_asset_upload_for_signing(%s,%s,%s,%s,%s,%s,%s)",
            (admin, seller, product, course, module, lesson, upload_id),
        ).fetchone()
        assert signing_row is not None
        signing = signing_row[0]
        connection.commit()

    upload = storage.presign_put(
        bucket=signing["bucket"],
        key=signing["object_key"],
        content_type=signing["content_type"],
        sha256_hex=signing["sha256"],
        byte_size=signing["byte_size"],
        upload_request_id=str(upload_id),
    )
    uploaded = httpx.put(upload.url, content=source_pdf, headers=upload.headers, timeout=10)
    assert uploaded.status_code in {200, 201}, uploaded.text
    source = storage.head(bucket=signing["bucket"], key=signing["object_key"])

    with psycopg.connect(str(RUNTIME_URL)) as connection:
        apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
        confirmed_row = connection.execute(
            """
            select app_private.confirm_asset_upload(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                upload_id,
                source.version_id,
                source.size,
                source_sha,
                source.etag,
                uuid4(),
                hashlib.sha256(b"confirm vertical").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
        assert confirmed_row is not None
        confirmed = confirmed_row[0]
        connection.commit()
        assert confirmed["status"] == "uploaded"

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
    downloaded = httpx.get(signed.url, timeout=10)
    assert downloaded.status_code == 200
    assert len(downloaded.content) == authorized[5]
    assert hashlib.sha256(downloaded.content).hexdigest() == authorized[6]
    assert downloaded.headers["cache-control"] == "private, no-store, max-age=0"
    assert downloaded.headers["content-disposition"] == (
        "inline; filename*=UTF-8''vertical-real.pdf"
    )


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
