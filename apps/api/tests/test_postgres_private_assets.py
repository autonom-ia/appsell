from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest
from psycopg.types.json import Jsonb

from appsell_api.reset_demo_tenant import (
    ASSET_WORKER_EMAIL,
    ASSET_WORKER_SUBJECT,
    load_manifest,
    prepare_demo_storage_cleanup,
    reset_demo,
)
from appsell_api.storage_s3 import MULTIPART_PART_BYTES

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
ISSUER = "https://identity.demo.test.autonomia.site"

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)


def identity_id(subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{ISSUER}\0{subject}")


def apply_context(
    connection: psycopg.Connection[Any], actor: UUID, seller: UUID | None, capability: str
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


@pytest.fixture(autouse=True)
def pristine_demo() -> Generator[None, None, None]:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.commit()
    yield
    seller = UUID(manifest["seller"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("select set_config('appsell.demo_reset_key','autonomia-demo-v1',true)")
        connection.execute(
            "delete from publication_asset where seller_organization_id=%s and storage_driver='s3'",
            (seller,),
        )
        connection.execute(
            "delete from lesson_asset_draft draft where draft.seller_organization_id=%s "
            "and exists(select 1 from asset_blob blob "
            "where blob.seller_organization_id=draft.seller_organization_id "
            "and blob.id=draft.blob_id and blob.storage_driver='s3')",
            (seller,),
        )
        connection.execute(
            "delete from asset_pdf_page page where page.seller_organization_id=%s "
            "and exists(select 1 from asset_pdf_raster raster "
            "join asset_blob blob "
            "on blob.seller_organization_id=raster.seller_organization_id "
            "and blob.id=raster.blob_id "
            "where raster.seller_organization_id=page.seller_organization_id "
            "and raster.asset_id=page.asset_id "
            "and raster.asset_version=page.asset_version "
            "and blob.storage_driver='s3')",
            (seller,),
        )
        connection.execute(
            "delete from asset_pdf_raster raster where raster.seller_organization_id=%s "
            "and exists(select 1 from asset_blob blob "
            "where blob.seller_organization_id=raster.seller_organization_id "
            "and blob.id=raster.blob_id and blob.storage_driver='s3')",
            (seller,),
        )
        connection.execute(
            "delete from asset_version version where version.seller_organization_id=%s "
            "and exists(select 1 from asset_blob blob "
            "where blob.seller_organization_id=version.seller_organization_id "
            "and blob.id=version.blob_id and blob.storage_driver='s3')",
            (seller,),
        )
        connection.execute(
            "delete from asset_blob where seller_organization_id=%s and storage_driver='s3'",
            (seller,),
        )
        connection.execute(
            "delete from app_private.asset_storage_cleanup_job where seller_organization_id=%s",
            (seller,),
        )
        connection.execute(
            "delete from app_private.asset_verification_job where seller_organization_id=%s",
            (seller,),
        )
        connection.execute(
            "delete from app_private.asset_pdf_page_candidate where seller_organization_id=%s",
            (seller,),
        )
        connection.execute(
            "delete from app_private.asset_pdf_raster_candidate where seller_organization_id=%s",
            (seller,),
        )
        connection.execute(
            "delete from app_private.asset_upload where seller_organization_id=%s",
            (seller,),
        )
        connection.execute(
            "delete from asset_record record where record.seller_organization_id=%s "
            "and not exists(select 1 from learning_asset legacy "
            "where legacy.seller_organization_id=record.seller_organization_id "
            "and legacy.id=record.id)",
            (seller,),
        )
        connection.commit()


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def pdf_page_manifest(
    *, seller: UUID, created: dict[str, Any], byte_size: int = 1024
) -> list[dict[str, object]]:
    return [
        {
            "page_number": 1,
            "storage_driver": "s3",
            "storage_bucket": "appsell-ready",
            "object_key": (
                f"tenants/{seller}/assets/{created['asset_id']}"
                f"/v{created['reserved_version']}/pages/1.png"
            ),
            "storage_version_id": "page-version-1",
            "storage_etag": "page-etag-1",
            "byte_size": byte_size,
            "sha256": digest(f"pdf-page-{created['upload_id']}"),
            "pixel_width": 1200,
            "pixel_height": 1600,
        }
    ]


def ensure_worker(subject: str = ASSET_WORKER_SUBJECT, email: str = ASSET_WORKER_EMAIL) -> UUID:
    assert ADMIN_URL
    worker = identity_id(subject)
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,%s,%s,%s,true)
            on conflict(id) do nothing
            """,
            (worker, ISSUER, subject, email),
        )
        connection.execute(
            """
            insert into app_private.asset_worker_identity(identity_user_id,status,label)
            values(%s,'active','PostgreSQL test worker')
            on conflict(identity_user_id) do update set status='active'
            """,
            (worker,),
        )
        connection.commit()

    return worker


def upload_ready_asset(
    *,
    admin: UUID,
    seller: UUID,
    product: UUID,
    course: UUID,
    module: UUID,
    lesson: UUID,
    course_version: int,
    existing_asset: UUID | None = None,
    media_type: str = "application/pdf",
    file_name: str = "guia-privado.pdf",
    source_size: int = 1024,
    ready_size: int = 900,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assert RUNTIME_URL
    worker = ensure_worker()
    payload_hash = digest(f"upload-{uuid4()}")
    asset_hash = digest(f"asset-{uuid4()}")
    ready_hash = digest(f"normalized-{uuid4()}")
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        created_row = connection.execute(
            """
            select app_private.create_asset_upload_intent(
              %s,%s,%s,%s,%s,%s,%s,%s,'Material privado',%s,
              %s,%s,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s
            )
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                existing_asset,
                course_version,
                file_name,
                media_type,
                source_size,
                asset_hash,
                uuid4(),
                payload_hash,
                uuid4(),
            ),
        ).fetchone()
        assert created_row is not None
        created = created_row[0]
        connection.commit()
        assert "quarantine_key" not in created and "ready_key" not in created

        apply_context(connection, admin, seller, "catalog.asset.upload.sign")
        signing_row = connection.execute(
            "select app_private.read_asset_upload_for_signing(%s,%s,%s,%s,%s,%s,%s)",
            (admin, seller, product, course, module, lesson, created["upload_id"]),
        ).fetchone()
        assert signing_row is not None
        signing = signing_row[0]
        connection.commit()
        assert signing["object_key"].startswith(f"tenants/{seller}/quarantine/")
        assert signing["bucket"] == "appsell-quarantine"

        confirm_key = uuid4()
        confirm_body = digest("confirm" + payload_hash)
        apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
        connection.execute(
            """
            select app_private.confirm_asset_upload(
              %s,%s,%s,%s,%s,%s,%s,'source-version-1',%s,%s,'source-etag',%s,%s,%s
            )
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                created["upload_id"],
                source_size,
                asset_hash,
                confirm_key,
                confirm_body,
                uuid4(),
            ),
        )
        connection.commit()
        apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
        lost_response_row = connection.execute(
            """
            select app_private.replay_asset_upload_confirmation(
              %s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                created["upload_id"],
                confirm_key,
                confirm_body,
            ),
        ).fetchone()
        assert lost_response_row is not None
        lost_response = lost_response_row[0]
        connection.commit()
        assert lost_response["confirmed"] is True
        created["_confirm_key"] = str(confirm_key)
        created["_confirm_body"] = confirm_body

        apply_context(connection, admin, seller, "catalog.asset.verify")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.begin_asset_verification(%s,%s,%s,'seller-session',60)",
                (admin, seller, created["upload_id"]),
            )
        connection.rollback()

        apply_context(connection, worker, seller, "catalog.asset.verify")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.begin_asset_verification(%s,%s,%s,'worker-with-tenant',60)",
                (worker, seller, created["upload_id"]),
            )
        connection.rollback()

        apply_context(connection, worker, None, "catalog.asset.verify")
        lease_row = connection.execute(
            "select app_private.begin_asset_verification(%s,%s,%s,'scanner-test',60)",
            (worker, seller, created["upload_id"]),
        ).fetchone()
        assert lease_row is not None
        lease = lease_row[0]
        connection.commit()
        assert lease["ready_key"].startswith(f"tenants/{seller}/assets/")
        assert lease["quarantine_bucket"] == "appsell-quarantine"
        assert lease["ready_bucket"] == "appsell-ready"

        apply_context(connection, worker, None, "catalog.asset.verify")
        pdf_manifest_sha: str | None = None
        if media_type == "application/pdf":
            registered_row = connection.execute(
                """
                select app_private.register_asset_ready_candidate_v2(
                  %s,%s,%s,'scanner-test','ready-version-1','ready-etag',%s,%s,
                  %s,%s,'clean',%s)
                """,
                (
                    worker,
                    seller,
                    created["upload_id"],
                    ready_size,
                    ready_hash,
                    media_type,
                    media_type,
                    Jsonb(pdf_page_manifest(seller=seller, created=created)),
                ),
            ).fetchone()
            assert registered_row is not None
            pdf_manifest_sha = registered_row[0]["pdf_manifest_sha256"]
        else:
            connection.execute(
                """
                select app_private.register_asset_ready_candidate(
                  %s,%s,%s,'scanner-test','ready-version-1','ready-etag',%s,%s,
                  %s,%s,'clean')
                """,
                (
                    worker,
                    seller,
                    created["upload_id"],
                    ready_size,
                    ready_hash,
                    media_type,
                    media_type,
                ),
            )
        connection.commit()

        finalize_key = uuid4()
        finalize_body = digest("finalize" + payload_hash)
        apply_context(connection, worker, None, "catalog.asset.verify")
        finalize_function = (
            "app_private.finalize_asset_verification_v2"
            if media_type == "application/pdf"
            else "app_private.finalize_asset_verification"
        )
        pdf_manifest_placeholder = ",%s" if pdf_manifest_sha else ""
        finalized_row = connection.execute(
            f"""
            select {finalize_function}(
              %s,%s,%s,'scanner-test',%s,%s,%s,%s,%s,%s,
              'clean','ready-version-1','ready-etag','ready',%s,%s,%s{pdf_manifest_placeholder}
            )
            """,  # noqa: S608 - function name and optional placeholder are fixed above
            (
                worker,
                seller,
                created["upload_id"],
                source_size,
                asset_hash,
                ready_size,
                ready_hash,
                media_type,
                media_type,
                finalize_key,
                finalize_body,
                uuid4(),
                *((pdf_manifest_sha,) if pdf_manifest_sha else ()),
            ),
        ).fetchone()
        assert finalized_row is not None
        finalized = finalized_row[0]
        connection.commit()
        apply_context(connection, worker, None, "catalog.asset.verify")
        replay_row = connection.execute(
            f"""
            select {finalize_function}(
              %s,%s,%s,'scanner-test',%s,%s,%s,%s,%s,%s,
              'clean','ready-version-1','ready-etag','ready',%s,%s,%s{pdf_manifest_placeholder}
            )
            """,  # noqa: S608 - function name and optional placeholder are fixed above
            (
                worker,
                seller,
                created["upload_id"],
                source_size,
                asset_hash,
                ready_size,
                ready_hash,
                media_type,
                media_type,
                finalize_key,
                finalize_body,
                uuid4(),
                *((pdf_manifest_sha,) if pdf_manifest_sha else ()),
            ),
        ).fetchone()
        assert replay_row is not None
        replay = replay_row[0]
        connection.commit()
        apply_context(connection, admin, seller, "catalog.asset.read")
        status_row = connection.execute(
            "select app_private.read_asset_upload_status(%s,%s,%s,%s,%s,%s,%s)",
            (admin, seller, product, course, module, lesson, created["upload_id"]),
        ).fetchone()
        assert status_row is not None
        status = status_row[0]
        listed_row = connection.execute(
            "select app_private.read_lesson_asset_uploads(%s,%s,%s,%s,%s,%s)",
            (admin, seller, product, course, module, lesson),
        ).fetchone()
        assert listed_row is not None
        listed = listed_row[0]
        connection.commit()
        serialized = json.dumps({"status": status, "listed": listed})
        assert status["status"] == "ready" and status["ready_asset"]["sha256"] == ready_hash
        assert "quarantine_key" not in serialized
        assert "ready_key" not in serialized
        assert "storage_bucket" not in serialized
        assert "storage_version" not in serialized
        assert "storage_etag" not in serialized
        assert "expected_sha" not in serialized
        apply_context(connection, admin, seller, "catalog.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_asset_upload_status(%s,%s,%s,%s,%s,%s,%s)",
                (admin, seller, product, course, uuid4(), lesson, created["upload_id"]),
            )
        connection.rollback()
        apply_context(connection, admin, seller, "catalog.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_asset_upload_status(%s,%s,%s,%s,%s,%s,%s)",
                (admin, seller, product, course, module, uuid4(), created["upload_id"]),
            )
        connection.rollback()
        other_seller = uuid4()
        apply_context(connection, admin, other_seller, "catalog.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_asset_upload_status(%s,%s,%s,%s,%s,%s,%s)",
                (admin, other_seller, product, course, module, lesson, created["upload_id"]),
            )
        connection.rollback()
    assert finalized["ready"] is True and finalized["applied"] is True
    assert replay["ready"] is True and replay["applied"] is False
    finalized["_ready_hash"] = ready_hash
    return created, finalized


def test_demo_reset_pins_the_verified_video_to_the_baseline_publication() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    publication = UUID(manifest["publication"]["id"])
    video = manifest["video"]
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select blob.media_type,blob.magic_type,blob.byte_size,blob.sha256,
              published.media_type,published.byte_size,published.sha256,published.position
            from asset_blob blob
            join publication_asset published
              on published.seller_organization_id=blob.seller_organization_id
             and published.blob_id=blob.id
            where blob.seller_organization_id=%s and published.publication_id=%s
              and published.asset_id=%s
            """,
            (seller, publication, UUID(video["asset_id"])),
        ).fetchone()
    assert state == (
        "video/mp4",
        "video/mp4",
        video["bytes"],
        video["sha256"],
        "video/mp4",
        video["bytes"],
        video["sha256"],
        video["position"],
    )


def test_verified_mp4_uses_the_same_tenant_pinned_asset_state_machine() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][1]["lesson_id"])

    created, finalized = upload_ready_asset(
        admin=admin,
        seller=seller,
        product=product,
        course=course,
        module=module,
        lesson=lesson,
        course_version=1,
        media_type="video/mp4",
        file_name="aula-segura.mp4",
        source_size=2_000_000,
        ready_size=2_000_000,
    )
    with psycopg.connect(ADMIN_URL) as connection:
        media = connection.execute(
            """
            select media_type,magic_type,byte_size,scanner_result
            from asset_blob where seller_organization_id=%s and id=%s
            """,
            (seller, UUID(finalized["blob_id"])),
        ).fetchone()
        upload = connection.execute(
            """
            select expected_media_type,expected_byte_size,status
            from app_private.asset_upload where seller_organization_id=%s and id=%s
            """,
            (seller, UUID(created["upload_id"])),
        ).fetchone()
    assert media == ("video/mp4", "video/mp4", 2_000_000, "clean")
    assert upload == ("video/mp4", 2_000_000, "ready")


def test_verified_versions_are_immutable_tenant_scoped_and_publication_pinned() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    offer = UUID(manifest["offer"]["id"])

    created, first = upload_ready_asset(
        admin=admin,
        seller=seller,
        product=product,
        course=course,
        module=module,
        lesson=lesson,
        course_version=1,
    )
    asset = UUID(created["asset_id"])
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select draft_version from course where seller_organization_id=%s and id=%s),
              (select count(*) from asset_blob where seller_organization_id=%s and id=%s),
              (select count(*) from asset_version where seller_organization_id=%s and asset_id=%s),
              (select storage_bucket from asset_blob
                where seller_organization_id=%s and id=%s),
              has_table_privilege('appsell_runtime','asset_blob','SELECT'),
              has_table_privilege('appsell_runtime','lesson_asset_draft','UPDATE'),
              has_table_privilege('appsell_runtime','publication_asset','SELECT')
            """,
            (
                seller,
                course,
                seller,
                UUID(first["blob_id"]),
                seller,
                asset,
                seller,
                UUID(first["blob_id"]),
            ),
        ).fetchone()
    assert state == (2, 1, 1, "appsell-ready", False, False, False)

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.publication.create")
        publication_one = connection.execute(
            "select * from app_private.publish_product(%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
            (
                admin,
                seller,
                product,
                offer,
                json.dumps({str(course): 2}),
                uuid4(),
                digest("publication-one"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert publication_one is not None and publication_one[4] is True

    second_created, second = upload_ready_asset(
        admin=admin,
        seller=seller,
        product=product,
        course=course,
        module=module,
        lesson=lesson,
        course_version=2,
        existing_asset=asset,
    )
    assert second["asset_version"] == 2 and second["course_version"] == 3
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """
                select app_private.replay_asset_upload_confirmation(
                  %s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    second_created["upload_id"],
                    UUID(created["_confirm_key"]),
                    created["_confirm_body"],
                ),
            )
        connection.rollback()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.publication.create")
        publication_two = connection.execute(
            "select * from app_private.publish_product(%s,%s,%s,%s,2,1,%s::jsonb,%s,%s,%s)",
            (
                admin,
                seller,
                product,
                offer,
                json.dumps({str(course): 3}),
                uuid4(),
                digest("publication-two"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert publication_two is not None and publication_two[4] is True

    with psycopg.connect(ADMIN_URL) as connection:
        versions = connection.execute(
            """
            select publication_id,asset_version from publication_asset
            where seller_organization_id=%s and asset_id=%s
              and publication_id in (%s,%s) order by publication_id
            """,
            (seller, asset, publication_one[0], publication_two[0]),
        ).fetchall()
        assert {row[1] for row in versions} == {1, 2}
        pinned_buyer = uuid4()
        pinned_entitlement = uuid4()
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,%s,%s,%s,true)
            """,
            (
                pinned_buyer,
                ISSUER,
                f"asset-pin-{pinned_buyer}",
                f"asset-pin-{pinned_buyer}@autonomia.site",
            ),
        )
        connection.execute(
            """
            insert into buyer_entitlement(
              seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
              publication_id,reference_key,source,status
            ) values(%s,%s,%s,%s,%s,%s,'manual','active')
            """,
            (
                seller,
                pinned_entitlement,
                pinned_buyer,
                product,
                publication_one[0],
                f"asset-pin-{pinned_entitlement}",
            ),
        )
        connection.commit()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "update asset_blob set storage_etag='tampered' "
                "where seller_organization_id=%s and id=%s",
                (seller, UUID(first["blob_id"])),
            )

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, pinned_buyer, None, "library.asset.read")
        authorized = connection.execute(
            "select * from app_private.authorize_publication_asset(%s,%s,%s)",
            (pinned_buyer, pinned_entitlement, asset),
        ).fetchone()
        connection.rollback()
        apply_context(connection, pinned_buyer, None, "learning.player.read")
        player = connection.execute(
            "select app_private.read_learning_player_v2(%s,%s,%s)",
            (pinned_buyer, pinned_entitlement, lesson),
        ).fetchone()
    assert authorized is not None and authorized[1] == 1
    assert authorized[7] == "s3"
    assert authorized[5] == 900 and authorized[6] == first["_ready_hash"]
    assert player is not None
    assert player[0]["publication"]["id"] == str(publication_one[0])
    assert player[0]["selected_asset"] == player[0]["selected_assets"][0]
    assert str(asset) in {item["id"] for item in player[0]["selected_assets"]}


def test_rejected_verification_never_creates_blob_version_or_draft() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    worker = ensure_worker()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    expected_sha = digest("rejected-source")
    normalized_rejected_sha = digest("normalized-rejected")
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        created_row = connection.execute(
            """
            select app_private.create_asset_upload_intent(
              %s,%s,%s,%s,%s,%s,null,1,'Rejeitado','rejeitado.pdf','application/pdf',
              256,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s
            )
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                expected_sha,
                uuid4(),
                digest("c"),
                uuid4(),
            ),
        ).fetchone()
        assert created_row is not None
        created = created_row[0]
        connection.commit()
        apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
        connection.execute(
            "select app_private.confirm_asset_upload("
            "%s,%s,%s,%s,%s,%s,%s,'v1',256,%s,'etag',%s,%s,%s)",
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                created["upload_id"],
                expected_sha,
                uuid4(),
                digest("d"),
                uuid4(),
            ),
        )
        connection.commit()
        apply_context(connection, worker, None, "catalog.asset.verify")
        connection.execute(
            "select app_private.begin_asset_verification(%s,%s,%s,'scanner-test',60)",
            (worker, seller, created["upload_id"]),
        )
        connection.commit()
        apply_context(connection, worker, None, "catalog.asset.verify")
        registered_row = connection.execute(
            """
            select app_private.register_asset_ready_candidate_v2(
              %s,%s,%s,'scanner-test','ready-v','ready-etag',200,%s,
              'application/pdf','application/pdf','clean',%s)
            """,
            (
                worker,
                seller,
                created["upload_id"],
                normalized_rejected_sha,
                Jsonb(pdf_page_manifest(seller=seller, created=created)),
            ),
        ).fetchone()
        assert registered_row is not None
        pdf_manifest_sha = registered_row[0]["pdf_manifest_sha256"]
        connection.commit()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update lesson set status='archived' where seller_organization_id=%s and id=%s",
            (seller, lesson),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker, None, "catalog.asset.verify")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                """
                select app_private.finalize_asset_verification_v2(
                  %s,%s,%s,'scanner-test',256,%s,200,%s,'application/pdf',
                  'application/pdf','clean','ready-v','ready-etag','ready',%s,%s,%s,%s)
                """,
                (
                    worker,
                    seller,
                    created["upload_id"],
                    expected_sha,
                    normalized_rejected_sha,
                    uuid4(),
                    digest("archived-finalize"),
                    uuid4(),
                    pdf_manifest_sha,
                ),
            )
        connection.rollback()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update lesson set status='active' where seller_organization_id=%s and id=%s",
            (seller, lesson),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker, None, "catalog.asset.verify")
        rejected_row = connection.execute(
            """
            select app_private.finalize_asset_verification_v2(
              %s,%s,%s,'scanner-test',255,%s,200,%s,'application/pdf','application/pdf',
              'clean','ready-v','ready-etag','ready',%s,%s,%s,%s)
            """,
            (
                worker,
                seller,
                created["upload_id"],
                expected_sha,
                normalized_rejected_sha,
                uuid4(),
                digest("e"),
                uuid4(),
                pdf_manifest_sha,
            ),
        ).fetchone()
        assert rejected_row is not None
        rejected = rejected_row[0]
        connection.commit()
    assert rejected["ready"] is False and rejected["status"] == "rejected"
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from asset_blob where seller_organization_id=%s and id=%s),
              (select count(*) from asset_version where seller_organization_id=%s and asset_id=%s),
              (select count(*) from lesson_asset_draft
                where seller_organization_id=%s and asset_id=%s),
              (select count(*) from asset_pdf_raster
                where seller_organization_id=%s and asset_id=%s),
              (select count(*) from asset_pdf_page
                where seller_organization_id=%s and asset_id=%s),
              (select draft_version from course where seller_organization_id=%s and id=%s),
              (select count(*) from app_private.asset_storage_cleanup_job
                where seller_organization_id=%s and upload_id=%s
                  and kind='ready_orphan' and storage_version_id='ready-v' and status='queued'),
              (select count(*) from app_private.asset_storage_cleanup_job
                where seller_organization_id=%s and upload_id=%s
                  and kind='pdf_page_orphan' and storage_version_id='page-version-1'
                  and status='queued')
            """,
            (
                seller,
                UUID(created["blob_id"]),
                seller,
                UUID(created["asset_id"]),
                seller,
                UUID(created["asset_id"]),
                seller,
                UUID(created["asset_id"]),
                seller,
                UUID(created["asset_id"]),
                seller,
                course,
                seller,
                UUID(created["upload_id"]),
                seller,
                UUID(created["upload_id"]),
            ),
        ).fetchone()
    assert state == (0, 0, 0, 0, 0, 1, 1, 1)


def test_open_upload_quota_is_atomic_under_concurrency() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                select app_private.create_asset_upload_intent(
                  %s,%s,%s,%s,%s,%s,null,1,'Same bucket','same-bucket.pdf',
                  'application/pdf',128,%s,'s3','same-bucket','same-bucket',%s,%s,%s)
                """,
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    digest("same-bucket"),
                    uuid4(),
                    digest("same-bucket-body"),
                    uuid4(),
                ),
            )
        connection.rollback()

    def create_intent(index: int, barrier: Barrier | None = None) -> str:
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                apply_context(connection, admin, seller, "catalog.asset.create")
                if barrier is not None:
                    barrier.wait(timeout=5)
                connection.execute(
                    """
                    select app_private.create_asset_upload_intent(
                      %s,%s,%s,%s,%s,%s,null,1,%s,%s,'application/pdf',128,%s,
                      's3','appsell-quarantine','appsell-ready',%s,%s,%s)
                    """,
                    (
                        admin,
                        seller,
                        product,
                        course,
                        module,
                        lesson,
                        f"Quota {index}",
                        f"quota-{index}.pdf",
                        digest(f"quota-asset-{index}"),
                        uuid4(),
                        digest(f"quota-body-{index}"),
                        uuid4(),
                    ),
                )
                connection.commit()
            return "created"
        except psycopg.DatabaseError as error:
            return error.sqlstate or "database_error"

    assert all(create_intent(index) == "created" for index in range(9))
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda index: create_intent(index, barrier), (9, 10)))
    assert sorted(outcomes) == ["P4290", "created"]
    assert ADMIN_URL is not None
    with psycopg.connect(ADMIN_URL) as connection:
        uploads = connection.execute(
            """
            select id from app_private.asset_upload
            where seller_organization_id=%s and title like 'Quota %%'
            order by id
            """,
            (seller,),
        ).fetchall()
    assert len(uploads) == 10
    for (upload_id,) in uploads:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, admin, seller, "catalog.asset.upload.cancel")
            connection.execute(
                "select app_private.cancel_asset_upload(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    upload_id,
                    uuid4(),
                    digest(f"cancel-{upload_id}"),
                    uuid4(),
                ),
            )
            connection.commit()
    assert create_intent(11) == "P4290"
    worker = ensure_worker()
    assert ADMIN_URL is not None
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            update app_private.asset_storage_cleanup_job
            set available_at=clock_timestamp()
            where seller_organization_id=%s and kind='quarantine_reconcile'
            """,
            (seller,),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker, None, "catalog.asset.verify")
        reconciles_row = connection.execute(
            "select app_private.claim_asset_storage_cleanup_jobs(%s,'reconcile-worker',20,30)",
            (worker,),
        ).fetchone()
        assert reconciles_row is not None
        reconciles = reconciles_row[0]
        connection.commit()
    assert len(reconciles) == 10
    assert all(job["kind"] == "quarantine_reconcile" for job in reconciles)
    assert all(job["delete_mode"] == "delete_all_versions" for job in reconciles)
    assert all(job["storage_version_id"] is None for job in reconciles)
    for job in reconciles:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, worker, None, "catalog.asset.verify")
            connection.execute(
                "select app_private.complete_asset_storage_cleanup(%s,'reconcile-worker',%s,%s)",
                (worker, seller, UUID(job["id"])),
            )
            connection.commit()
    assert create_intent(12) == "created"


@pytest.mark.parametrize(
    "file_name",
    ["folder/material.pdf", "folder\\material.pdf", "line\nbreak.pdf"],
)
def test_upload_intent_rejects_path_separators_and_control_characters(file_name: str) -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                select app_private.create_asset_upload_intent(
                  %s,%s,%s,%s,%s,%s,null,1,'Unsafe name',%s,
                  'application/pdf',128,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s)
                """,
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    file_name,
                    digest("unsafe-file"),
                    uuid4(),
                    digest("unsafe-file-body"),
                    uuid4(),
                ),
            )


def test_cancel_and_finalize_are_linearizable_and_never_leave_partial_rows() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    worker = ensure_worker()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    source_hash = digest("cancel-race-source")
    ready_hash = digest("cancel-race-ready")
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        created_row = connection.execute(
            """
            select app_private.create_asset_upload_intent(
              %s,%s,%s,%s,%s,%s,null,1,'Cancel race','cancel-race.pdf',
              'application/pdf',512,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s)
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                source_hash,
                uuid4(),
                digest("r1"),
                uuid4(),
            ),
        ).fetchone()
        assert created_row is not None
        created = created_row[0]
        connection.commit()
        apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
        connection.execute(
            "select app_private.confirm_asset_upload("
            "%s,%s,%s,%s,%s,%s,%s,'source-v',512,%s,'etag',%s,%s,%s)",
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                created["upload_id"],
                source_hash,
                uuid4(),
                digest("r2"),
                uuid4(),
            ),
        )
        connection.commit()
        apply_context(connection, worker, None, "catalog.asset.verify")
        connection.execute(
            "select app_private.begin_asset_verification(%s,%s,%s,'scanner-race',60)",
            (worker, seller, created["upload_id"]),
        )
        connection.commit()
        apply_context(connection, worker, None, "catalog.asset.verify")
        registered_row = connection.execute(
            """
            select app_private.register_asset_ready_candidate_v2(
              %s,%s,%s,'scanner-race','ready-v','ready-etag',480,%s,
              'application/pdf','application/pdf','clean',%s)
            """,
            (
                worker,
                seller,
                created["upload_id"],
                ready_hash,
                Jsonb(pdf_page_manifest(seller=seller, created=created)),
            ),
        ).fetchone()
        assert registered_row is not None
        pdf_manifest_sha = registered_row[0]["pdf_manifest_sha256"]
        connection.commit()

    barrier = Barrier(2)

    def cancel() -> str:
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                apply_context(connection, admin, seller, "catalog.asset.upload.cancel")
                barrier.wait(timeout=5)
                connection.execute(
                    "select app_private.cancel_asset_upload(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        admin,
                        seller,
                        product,
                        course,
                        module,
                        lesson,
                        created["upload_id"],
                        uuid4(),
                        digest("cancel"),
                        uuid4(),
                    ),
                )
                connection.commit()
            return "cancelled"
        except psycopg.DatabaseError as error:
            return error.sqlstate or "database_error"

    def finalize() -> str:
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                apply_context(connection, worker, None, "catalog.asset.verify")
                barrier.wait(timeout=5)
                connection.execute(
                    """
                    select app_private.finalize_asset_verification_v2(
                      %s,%s,%s,'scanner-race',512,%s,480,%s,'application/pdf',
                      'application/pdf','clean','ready-v','ready-etag','ready',%s,%s,%s,%s)
                    """,
                    (
                        worker,
                        seller,
                        created["upload_id"],
                        source_hash,
                        ready_hash,
                        uuid4(),
                        digest("finalize"),
                        uuid4(),
                        pdf_manifest_sha,
                    ),
                )
                connection.commit()
            return "ready"
        except psycopg.DatabaseError as error:
            return error.sqlstate or "database_error"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(cancel), executor.submit(finalize))
        outcomes = {future.result() for future in futures}
    assert outcomes in ({"cancelled", "P4091"}, {"ready", "P4091"})
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select upload.status,
              (select count(*) from asset_blob
                where seller_organization_id=%s and id=upload.blob_id),
              (select count(*) from asset_version where seller_organization_id=%s
                and asset_id=upload.asset_id),
              (select count(*) from lesson_asset_draft where seller_organization_id=%s
                and asset_id=upload.asset_id),
              (select count(*) from asset_pdf_raster where seller_organization_id=%s
                and asset_id=upload.asset_id),
              (select count(*) from asset_pdf_page where seller_organization_id=%s
                and asset_id=upload.asset_id),
              (select status from app_private.asset_storage_cleanup_job cleanup
                where cleanup.seller_organization_id=upload.seller_organization_id
                  and cleanup.upload_id=upload.id and cleanup.kind='ready_orphan'
                  and cleanup.storage_version_id='ready-v'),
              (select status from app_private.asset_storage_cleanup_job cleanup
                where cleanup.seller_organization_id=upload.seller_organization_id
                  and cleanup.upload_id=upload.id and cleanup.kind='pdf_page_orphan'
                  and cleanup.storage_version_id='page-version-1')
            from app_private.asset_upload upload
            where upload.seller_organization_id=%s and upload.id=%s
            """,
            (seller, seller, seller, seller, seller, seller, UUID(created["upload_id"])),
        ).fetchone()
    assert state in (
        ("cancelled", 0, 0, 0, 0, 0, "queued", "queued"),
        ("ready", 1, 1, 1, 1, 1, "protected", "protected"),
    )


def test_claim_and_cancel_follow_upload_then_job_lock_order_without_deadlock() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    worker = ensure_worker()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    source_hash = digest("claim-cancel-source")

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        created_row = connection.execute(
            """
            select app_private.create_asset_upload_intent(
              %s,%s,%s,%s,%s,%s,null,1,'Claim cancel race','claim-cancel.pdf',
              'application/pdf',512,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s)
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                source_hash,
                uuid4(),
                digest("claim-cancel-create"),
                uuid4(),
            ),
        ).fetchone()
        assert created_row is not None
        created = created_row[0]
        connection.commit()
        apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
        connection.execute(
            "select app_private.confirm_asset_upload("
            "%s,%s,%s,%s,%s,%s,%s,'source-v',512,%s,'etag',%s,%s,%s)",
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                created["upload_id"],
                source_hash,
                uuid4(),
                digest("claim-cancel-confirm"),
                uuid4(),
            ),
        )
        connection.commit()

    barrier = Barrier(2)

    def claim() -> tuple[str, int | None]:
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                apply_context(connection, worker, None, "catalog.asset.verify")
                barrier.wait(timeout=5)
                claimed_row = connection.execute(
                    "select app_private.claim_asset_verification_jobs(%s,%s,1,30)",
                    (worker, "claim-cancel-worker"),
                ).fetchone()
                assert claimed_row is not None
                claimed = claimed_row[0]
                connection.commit()
            return "claimed", len(claimed)
        except psycopg.DatabaseError as error:
            return error.sqlstate or "database_error", None

    def cancel() -> str:
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                apply_context(connection, admin, seller, "catalog.asset.upload.cancel")
                barrier.wait(timeout=5)
                connection.execute(
                    "select app_private.cancel_asset_upload(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        admin,
                        seller,
                        product,
                        course,
                        module,
                        lesson,
                        created["upload_id"],
                        uuid4(),
                        digest("claim-cancel-cancel"),
                        uuid4(),
                    ),
                )
                connection.commit()
            return "cancelled"
        except psycopg.DatabaseError as error:
            return error.sqlstate or "database_error"

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim_future = executor.submit(claim)
        cancel_future = executor.submit(cancel)
        claim_outcome = claim_future.result()
        cancel_outcome = cancel_future.result()

    assert claim_outcome in (("claimed", 0), ("claimed", 1))
    assert cancel_outcome == "cancelled"
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select upload.status, verification.status
            from app_private.asset_upload upload
            join app_private.asset_verification_job verification
              on verification.seller_organization_id=upload.seller_organization_id
             and verification.upload_id=upload.id
            where upload.seller_organization_id=%s and upload.id=%s
            """,
            (seller, UUID(created["upload_id"])),
        ).fetchone()
    assert state == ("cancelled", "failed")
    ready_hash = digest("claim-cancel-ready")
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker, None, "catalog.asset.verify")
        with pytest.raises(psycopg.DatabaseError) as stale_lease:
            connection.execute(
                """
                select app_private.register_asset_ready_candidate_v2(
                  %s,%s,%s,'claim-cancel-worker','late-ready-v','late-ready-etag',480,%s,
                  'application/pdf','application/pdf','clean',%s)
                """,
                (
                    worker,
                    seller,
                    created["upload_id"],
                    ready_hash,
                    Jsonb(pdf_page_manifest(seller=seller, created=created)),
                ),
            )
        assert stale_lease.value.sqlstate == "P4091"
        connection.rollback()
    with psycopg.connect(ADMIN_URL) as connection:
        ready_reconcile = connection.execute(
            """
            select cleanup.status,cleanup.storage_version_id,cleanup.bucket,
              cleanup.object_key=upload.ready_key
            from app_private.asset_storage_cleanup_job cleanup
            join app_private.asset_upload upload
              on upload.seller_organization_id=cleanup.seller_organization_id
             and upload.id=cleanup.upload_id
            where cleanup.seller_organization_id=%s and cleanup.upload_id=%s
              and cleanup.kind='ready_reconcile'
            """,
            (seller, UUID(created["upload_id"])),
        ).fetchone()
    assert ready_reconcile == (
        "queued",
        None,
        "appsell-ready",
        True,
    )


def test_seller_open_and_ready_byte_launch_quotas_are_enforced() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    asset = UUID(manifest["lessons"][0]["asset_id"])

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into app_private.asset_upload(
              seller_organization_id,id,identity_user_id,catalog_product_id,course_id,
              module_id,lesson_id,asset_id,blob_id,reserved_version,title,file_name,
              expected_media_type,expected_byte_size,expected_sha256,storage_driver,
              quarantine_bucket,ready_bucket,quarantine_key,ready_key,status,expires_at
            ) select %s,gen_random_uuid(),%s,%s,%s,%s,%s,%s,gen_random_uuid(),ordinality+1,
              'Tenant quota','tenant-quota.pdf','application/pdf',128,
              encode(digest(ordinality::text,'sha256'),'hex'),'s3',
              'appsell-quarantine','appsell-ready','tenant-quarantine-'||ordinality,
              'tenant-ready-'||ordinality,'pending',clock_timestamp()+interval '10 minutes'
            from generate_series(1,100) input(ordinality)
            """,
            (seller, admin, product, course, module, lesson, asset),
        )
        connection.execute(
            """
            insert into app_private.asset_storage_cleanup_job(
              seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
              storage_version_id,status,available_at
            ) select seller_organization_id,id,'quarantine_reconcile','s3',
              quarantine_bucket,quarantine_key,null,'queued',expires_at+interval '5 minutes'
            from app_private.asset_upload
            where seller_organization_id=%s and title='Tenant quota'
            """,
            (seller,),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        with pytest.raises(psycopg.DatabaseError) as open_limit:
            connection.execute(
                """
                select app_private.create_asset_upload_intent(
                  %s,%s,%s,%s,%s,%s,null,1,'Over tenant','over-tenant.pdf',
                  'application/pdf',128,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s)
                """,
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    digest("over-tenant"),
                    uuid4(),
                    digest("over-tenant-body"),
                    uuid4(),
                ),
            )
        assert open_limit.value.sqlstate == "P4290"
        connection.rollback()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update app_private.asset_upload set status='cancelled' "
            "where seller_organization_id=%s and title='Tenant quota'",
            (seller,),
        )
        connection.execute(
            """
            insert into asset_blob(
              seller_organization_id,id,storage_driver,storage_bucket,object_key,
              storage_version_id,storage_etag,file_name,media_type,byte_size,sha256,
              magic_type,scanner_result,verified_at
            ) select %s,gen_random_uuid(),'s3','appsell-ready','quota/blob/'||ordinality,
              'version-'||ordinality,'etag-'||ordinality,'quota.pdf','application/pdf',
              10485760,encode(digest(('quota-'||ordinality)::text,'sha256'),'hex'),
              'application/pdf','clean',clock_timestamp()
            from generate_series(1,103) input(ordinality)
            """,
            (seller,),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        with pytest.raises(psycopg.DatabaseError) as byte_limit:
            connection.execute(
                """
                select app_private.create_asset_upload_intent(
                  %s,%s,%s,%s,%s,%s,null,1,'Over bytes','over-bytes.pdf',
                  'application/pdf',128,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s)
                """,
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    digest("over-bytes"),
                    uuid4(),
                    digest("over-bytes-body"),
                    uuid4(),
                ),
            )
        assert byte_limit.value.sqlstate == "P4290"


def test_workers_claim_distinct_jobs_recover_expired_leases_and_skip_cancelled() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    worker_one = ensure_worker()
    worker_two = ensure_worker("asset-worker-v2", "asset.worker.v2@autonomia.site")
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])

    def create_confirmed(index: int) -> dict[str, Any]:
        source_hash = digest(f"claim-source-{index}")
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, admin, seller, "catalog.asset.create")
            created_row = connection.execute(
                """
                select app_private.create_asset_upload_intent(
                  %s,%s,%s,%s,%s,%s,null,1,%s,%s,'application/pdf',256,%s,
                  's3','appsell-quarantine','appsell-ready',%s,%s,%s)
                """,
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    f"Claim {index}",
                    f"claim-{index}.pdf",
                    source_hash,
                    uuid4(),
                    digest(f"claim-create-{index}"),
                    uuid4(),
                ),
            ).fetchone()
            assert created_row is not None
            created = created_row[0]
            connection.commit()
            apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
            connection.execute(
                """
                select app_private.confirm_asset_upload(
                  %s,%s,%s,%s,%s,%s,%s,%s,256,%s,%s,%s,%s,%s)
                """,
                (
                    admin,
                    seller,
                    product,
                    course,
                    module,
                    lesson,
                    created["upload_id"],
                    f"source-version-{index}",
                    source_hash,
                    f"etag-{index}",
                    uuid4(),
                    digest(f"claim-confirm-{index}"),
                    uuid4(),
                ),
            )
            connection.commit()
        assert isinstance(created, dict)
        return created

    first, second, cancelled = (create_confirmed(index) for index in range(3))
    offer = UUID(manifest["offer"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            update app_private.asset_upload set
              created_at=clock_timestamp()-interval '20 minutes',
              expires_at=clock_timestamp()-interval '5 minutes'
            where seller_organization_id=%s and id in (%s,%s,%s)
            """,
            (
                seller,
                UUID(first["upload_id"]),
                UUID(second["upload_id"]),
                UUID(cancelled["upload_id"]),
            ),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.publication.create")
        with pytest.raises(psycopg.DatabaseError) as publication_blocked:
            connection.execute(
                "select * from app_private.publish_product(%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
                (
                    admin,
                    seller,
                    product,
                    offer,
                    json.dumps({str(course): 1}),
                    uuid4(),
                    digest("publish-expired-uploaded"),
                    uuid4(),
                ),
            )
        assert publication_blocked.value.sqlstate == "P4091"
        connection.rollback()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker_one, None, "catalog.asset.verify")
        assert connection.execute(
            "select app_private.claim_asset_storage_cleanup_jobs(%s,'active-source',20,30)",
            (worker_one,),
        ).fetchone() == ([],)
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.upload.cancel")
        connection.execute(
            """
            select app_private.cancel_asset_upload(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                cancelled["upload_id"],
                uuid4(),
                digest("cancel-claim-job"),
                uuid4(),
            ),
        )
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker_one, None, "catalog.asset.verify")
        cleanup_claim_row = connection.execute(
            "select app_private.claim_asset_storage_cleanup_jobs(%s,'cleanup-one',1,30)",
            (worker_one,),
        ).fetchone()
        assert cleanup_claim_row is not None
        cleanup_claim = cleanup_claim_row[0]
        connection.commit()
    assert len(cleanup_claim) == 1
    assert cleanup_claim[0]["upload_id"] == cancelled["upload_id"]
    assert cleanup_claim[0]["kind"] == "quarantine"
    cleanup_job = UUID(cleanup_claim[0]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update app_private.asset_storage_cleanup_job set "
            "lease_expires_at=clock_timestamp()-interval '1 second' "
            "where seller_organization_id=%s and id=%s",
            (seller, cleanup_job),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker_two, None, "catalog.asset.verify")
        cleanup_recovered_row = connection.execute(
            "select app_private.claim_asset_storage_cleanup_jobs(%s,'cleanup-two',1,30)",
            (worker_two,),
        ).fetchone()
        assert cleanup_recovered_row is not None
        cleanup_recovered = cleanup_recovered_row[0]
        connection.commit()
        assert cleanup_recovered[0]["attempt"] == 2
        apply_context(connection, worker_two, None, "catalog.asset.verify")
        assert connection.execute(
            "select app_private.retry_asset_storage_cleanup("
            "%s,'cleanup-two',%s,%s,1,'storage_unavailable')",
            (worker_two, seller, cleanup_job),
        ).fetchone() == ("queued",)
        connection.commit()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update app_private.asset_storage_cleanup_job set available_at=clock_timestamp() "
            "where seller_organization_id=%s and id=%s",
            (seller, cleanup_job),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker_one, None, "catalog.asset.verify")
        cleanup_third_row = connection.execute(
            "select app_private.claim_asset_storage_cleanup_jobs(%s,'cleanup-three',1,30)",
            (worker_one,),
        ).fetchone()
        assert cleanup_third_row is not None
        cleanup_third = cleanup_third_row[0]
        connection.commit()
        assert cleanup_third[0]["attempt"] == 3
        apply_context(connection, worker_one, None, "catalog.asset.verify")
        assert connection.execute(
            "select app_private.complete_asset_storage_cleanup(%s,'cleanup-three',%s,%s)",
            (worker_one, seller, cleanup_job),
        ).fetchone() == (True,)
        connection.commit()

    barrier = Barrier(2)

    def claim(actor: UUID, worker_name: str) -> list[dict[str, Any]]:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, actor, None, "catalog.asset.verify")
            barrier.wait(timeout=5)
            jobs_row = connection.execute(
                "select app_private.claim_asset_verification_jobs(%s,%s,1,30)",
                (actor, worker_name),
            ).fetchone()
            assert jobs_row is not None
            jobs = jobs_row[0]
            connection.commit()
        assert isinstance(jobs, list)
        assert all(isinstance(job, dict) for job in jobs)
        return jobs

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(claim, worker_one, "worker-instance-one"),
            executor.submit(claim, worker_two, "worker-instance-two"),
        )
        claimed = [future.result()[0] for future in futures]
    claimed_ids = {job["upload_id"] for job in claimed}
    assert claimed_ids == {first["upload_id"], second["upload_id"]}
    assert cancelled["upload_id"] not in claimed_ids
    assert all(job["quarantine_bucket"] == "appsell-quarantine" for job in claimed)
    assert all(job["ready_bucket"] == "appsell-ready" for job in claimed)
    assert {job["file_name"] for job in claimed} == {"claim-0.pdf", "claim-1.pdf"}

    crashed = claimed[0]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            update app_private.asset_verification_job
            set lease_expires_at=clock_timestamp()-interval '1 second'
            where seller_organization_id=%s and upload_id=%s
            """,
            (seller, UUID(crashed["upload_id"])),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker_two, None, "catalog.asset.verify")
        recovered_row = connection.execute(
            "select app_private.claim_asset_verification_jobs(%s,'recovery-worker',1,30)",
            (worker_two,),
        ).fetchone()
        assert recovered_row is not None
        recovered = recovered_row[0]
        connection.commit()
    assert len(recovered) == 1
    assert recovered[0]["upload_id"] == crashed["upload_id"]
    assert recovered[0]["attempt"] == 2
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            update app_private.asset_verification_job set attempts=10,
              lease_expires_at=clock_timestamp()-interval '1 second'
            where seller_organization_id=%s and upload_id=%s
            """,
            (seller, UUID(crashed["upload_id"])),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, worker_one, None, "catalog.asset.verify")
        assert connection.execute(
            "select app_private.claim_asset_verification_jobs(%s,'reaper-worker',20,30)",
            (worker_one,),
        ).fetchone() == ([],)
        connection.commit()
    with psycopg.connect(ADMIN_URL) as connection:
        exhausted = connection.execute(
            """
            select job.status,upload.status,upload.failure_code,
              (select count(*) from app_private.asset_storage_cleanup_job cleanup
                where cleanup.seller_organization_id=upload.seller_organization_id
                  and cleanup.upload_id=upload.id and cleanup.kind='quarantine')
            from app_private.asset_verification_job job
            join app_private.asset_upload upload
              on upload.seller_organization_id=job.seller_organization_id
             and upload.id=job.upload_id
            where job.seller_organization_id=%s and job.upload_id=%s
            """,
            (seller, UUID(crashed["upload_id"])),
        ).fetchone()
    assert exhausted == ("failed", "rejected", "verification_attempts_exhausted", 1)


def test_demo_reset_preserves_undrained_s3_references_fail_closed() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, manifest_sha = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, admin, seller, "catalog.asset.create")
        created_row = connection.execute(
            """
            select app_private.create_asset_upload_intent(
              %s,%s,%s,%s,%s,%s,null,1,'Reset guard','reset-guard.pdf',
              'application/pdf',128,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s)
            """,
            (
                admin,
                seller,
                product,
                course,
                module,
                lesson,
                digest("reset-guard"),
                uuid4(),
                digest("reset-guard-body"),
                uuid4(),
            ),
        ).fetchone()
        assert created_row is not None
        created = created_row[0]
        connection.commit()
    upload_id = UUID(created["upload_id"])
    asset_id = UUID(created["asset_id"])
    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(
            RuntimeError,
            match="demo reset blocked until private S3 storage cleanup is drained",
        ):
            reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.rollback()
        assert connection.execute(
            "select count(*) from app_private.asset_upload "
            "where seller_organization_id=%s and id=%s",
            (seller, upload_id),
        ).fetchone() == (1,)
        connection.execute(
            "update app_private.asset_storage_cleanup_job set status='completed' "
            "where seller_organization_id=%s and upload_id=%s",
            (seller, upload_id),
        )
        connection.execute(
            "delete from app_private.asset_storage_cleanup_job "
            "where seller_organization_id=%s and upload_id=%s",
            (seller, upload_id),
        )
        connection.execute(
            "delete from app_private.asset_upload where seller_organization_id=%s and id=%s",
            (seller, upload_id),
        )
        connection.execute(
            "delete from asset_record where seller_organization_id=%s and id=%s",
            (seller, asset_id),
        )
        connection.commit()
        orphan_blob = uuid4()
        connection.execute(
            """
            insert into asset_blob(
              seller_organization_id,id,storage_driver,storage_bucket,object_key,
              storage_version_id,storage_etag,file_name,media_type,byte_size,sha256,
              magic_type,scanner_result,verified_at
            ) values(%s,%s,'s3','appsell-ready','orphan/ready','orphan-version','etag',
              'orphan.pdf','application/pdf',128,%s,'application/pdf','clean',
              clock_timestamp())
            """,
            (seller, orphan_blob, digest("orphan-ready")),
        )
        connection.commit()
        with pytest.raises(
            RuntimeError,
            match="demo reset blocked until private S3 storage cleanup is drained",
        ):
            reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.rollback()
        connection.execute("select set_config('appsell.demo_reset_key','autonomia-demo-v1',true)")
        connection.execute(
            "delete from asset_blob where seller_organization_id=%s and id=%s",
            (seller, orphan_blob),
        )
        connection.commit()


def test_demo_storage_cleanup_prepare_makes_ready_assets_drainable_before_reset() -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    created, _ = upload_ready_asset(
        admin=admin,
        seller=seller,
        product=UUID(manifest["product"]["id"]),
        course=UUID(manifest["course"]["id"]),
        module=UUID(manifest["module"]["id"]),
        lesson=UUID(manifest["lessons"][0]["lesson_id"]),
        course_version=1,
    )
    upload_id = UUID(created["upload_id"])

    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(
            RuntimeError,
            match="demo reset blocked until private S3 storage cleanup is drained",
        ):
            reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.rollback()
        connection.execute(
            """
            update app_private.asset_storage_cleanup_job
            set status='dead',attempts=10,failure_code='attempts_exhausted'
            where seller_organization_id=%s and upload_id=%s and status<>'completed'
            """,
            (seller, upload_id),
        )
        connection.commit()
        pending = prepare_demo_storage_cleanup(connection, manifest)
        connection.commit()
        assert pending >= 2
        rows = connection.execute(
            """
            select kind,status from app_private.asset_storage_cleanup_job
            where seller_organization_id=%s and upload_id=%s
            order by kind
            """,
            (seller, upload_id),
        ).fetchall()
        assert ("quarantine_reconcile", "queued") in rows
        assert ("ready_reconcile", "queued") in rows
        assert ("ready_orphan", "queued") in rows
        assert all(status == "queued" for _, status in rows)
        connection.execute(
            "update app_private.asset_storage_cleanup_job set status='completed' "
            "where seller_organization_id=%s",
            (seller,),
        )
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.commit()


def _intencao_de_envio(
    connection: Any,
    *,
    admin: UUID,
    seller: UUID,
    product: UUID,
    course: UUID,
    module: UUID,
    lesson: UUID,
    byte_size: int,
) -> tuple[dict[str, Any], str]:
    """Uma intenção de envio de vídeo do tamanho pedido."""
    sha = digest(f"aula-{uuid4()}")
    apply_context(connection, admin, seller, "catalog.asset.create")
    linha = connection.execute(
        """
        select app_private.create_asset_upload_intent(
          %s,%s,%s,%s,%s,%s,null,1,'Aula longa','aula-longa.mp4',
          'video/mp4',%s,%s,'s3','appsell-quarantine','appsell-ready',%s,%s,%s)
        """,
        (
            admin,
            seller,
            product,
            course,
            module,
            lesson,
            byte_size,
            sha,
            uuid4(),
            digest(f"corpo-{uuid4()}"),
            uuid4(),
        ),
    ).fetchone()
    assert linha is not None
    connection.commit()
    return linha[0], sha


def _confirma(
    connection: Any,
    *,
    admin: UUID,
    seller: UUID,
    product: UUID,
    course: UUID,
    module: UUID,
    lesson: UUID,
    upload: str,
    observed_size: int,
    observed_checksum: str | None,
) -> dict[str, Any]:
    apply_context(connection, admin, seller, "catalog.asset.upload.confirm")
    linha = connection.execute(
        """
        select app_private.confirm_asset_upload(
          %s,%s,%s,%s,%s,%s,%s,'source-version-1',%s,%s,'source-etag',%s,%s,%s)
        """,
        (
            admin,
            seller,
            product,
            course,
            module,
            lesson,
            upload,
            observed_size,
            observed_checksum,
            uuid4(),
            digest(f"confirma-{uuid4()}"),
            uuid4(),
        ),
    ).fetchone()
    assert linha is not None
    connection.commit()
    resultado: dict[str, Any] = linha[0]
    return resultado


def _personas() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    manifest, _ = load_manifest()
    return (
        identity_id(manifest["personas"]["admin"]["subject"]),
        UUID(manifest["seller"]["id"]),
        UUID(manifest["product"]["id"]),
        UUID(manifest["course"]["id"]),
        UUID(manifest["module"]["id"]),
        UUID(manifest["lessons"][0]["lesson_id"]),
    )


def test_o_banco_e_o_codigo_concordam_sobre_o_tamanho_da_parte() -> None:
    """Quem decide se um envio foi fatiado é o banco, pelo tamanho declarado.

    Um sinalizador de quem chama poderia pedir para pular a conferência de um
    arquivo pequeno; o tamanho não. Isso obriga o número a existir dos dois
    lados, e obriga este teste a existir junto.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute("select app_private.asset_multipart_part_bytes()").fetchone()
    assert linha is not None
    assert linha[0] == MULTIPART_PART_BYTES


def test_envio_fatiado_confirma_sem_o_hash_do_arquivo_inteiro() -> None:
    """O envio em partes não produz esse hash, e o `confirm` sabe disso.

    A conferência de conteúdo dele é a da verificação, que relê todos os bytes
    contra o mesmo `expected_sha256`. O audit registra qual caminho foi usado,
    para que "adiada" nunca seja uma coisa que só o código sabe.
    """
    assert RUNTIME_URL
    admin, seller, product, course, module, lesson = _personas()
    tamanho = MULTIPART_PART_BYTES + 1024
    with psycopg.connect(RUNTIME_URL) as connection:
        criado, _ = _intencao_de_envio(
            connection,
            admin=admin,
            seller=seller,
            product=product,
            course=course,
            module=module,
            lesson=lesson,
            byte_size=tamanho,
        )
        resultado = _confirma(
            connection,
            admin=admin,
            seller=seller,
            product=product,
            course=course,
            module=module,
            lesson=lesson,
            upload=criado["upload_id"],
            observed_size=tamanho,
            observed_checksum=None,
        )
        assert resultado["status"] == "uploaded"
    # O audit é lido pela conexão administrativa: a do runtime enxerga só o que
    # a política do tenant deixa, e aqui o ponto é o registro existir.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as administrativa:
        registro = administrativa.execute(
            """
            select metadata->>'content_check' from audit_event
             where action='catalog.asset_upload_confirmed'
               and metadata->>'upload_id'=%s
            """,
            (str(criado["upload_id"]),),
        ).fetchone()
    assert registro is not None, "a confirmação não deixou registro no audit"
    assert registro[0] == "deferred_to_verification"


def test_envio_de_uma_parte_so_continua_exigindo_o_hash() -> None:
    """A conferência adiada não pode virar uma porta para pular a conferência.

    Se o hash pudesse faltar em qualquer envio, bastaria omiti-lo para promover
    um arquivo que não é o declarado até a verificação perceber.
    """
    assert RUNTIME_URL
    admin, seller, product, course, module, lesson = _personas()
    with psycopg.connect(RUNTIME_URL) as connection:
        criado, _ = _intencao_de_envio(
            connection,
            admin=admin,
            seller=seller,
            product=product,
            course=course,
            module=module,
            lesson=lesson,
            byte_size=1024,
        )
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _confirma(
                connection,
                admin=admin,
                seller=seller,
                product=product,
                course=course,
                module=module,
                lesson=lesson,
                upload=criado["upload_id"],
                observed_size=1024,
                observed_checksum=None,
            )


def test_envio_fatiado_com_tamanho_diferente_continua_sendo_recusado() -> None:
    """O tamanho continua conferido, exato, no `confirm`. Só o hash foi adiado."""
    assert RUNTIME_URL
    admin, seller, product, course, module, lesson = _personas()
    tamanho = MULTIPART_PART_BYTES + 1024
    with psycopg.connect(RUNTIME_URL) as connection:
        criado, _ = _intencao_de_envio(
            connection,
            admin=admin,
            seller=seller,
            product=product,
            course=course,
            module=module,
            lesson=lesson,
            byte_size=tamanho,
        )
        resultado = _confirma(
            connection,
            admin=admin,
            seller=seller,
            product=product,
            course=course,
            module=module,
            lesson=lesson,
            upload=criado["upload_id"],
            observed_size=tamanho - 1,
            observed_checksum=None,
        )
    assert resultado["status"] == "rejected"
    assert resultado["safe_reason"] == "upload_mismatch"


def test_a_cota_do_vendedor_cabe_um_curso_e_nao_uma_aula() -> None:
    """20 GiB, cerca de dezoito horas de curso.

    Antes eram 1 GiB. Vídeo de aula em 1080p ocupa perto de 1 GB por hora, então
    a regra antiga dizia, em linguagem de produto, que cada vendedor guardava
    **uma hora de aula no total**. Com o teto por arquivo em 512 MiB isso são
    duas aulas, e a terceira era recusada.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select app_private.seller_storage_quota_bytes(),"
            " app_private.seller_open_upload_quota_bytes()"
        ).fetchone()
    assert linha is not None
    assert linha[0] == 20 * 1024 * 1024 * 1024
    assert linha[1] == 4 * 1024 * 1024 * 1024


def test_a_cota_nao_e_executavel_por_qualquer_role() -> None:
    """O mesmo cuidado que faltou na `asset_multipart_part_bytes` da 0045."""
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            """
            select has_function_privilege('public',
                     'app_private.seller_storage_quota_bytes()','execute'),
                   has_function_privilege('appsell_runtime',
                     'app_private.seller_open_upload_quota_bytes()','execute')
            """
        ).fetchone()
    assert linha == (False, True)


def test_os_bytes_em_voo_tem_teto_proprio() -> None:
    """A cota de envios abertos contava quantidade, e quantidade era a coisa errada.

    Cem envios eram cem envios, com o arquivo em 100 MiB ou em 512 MiB. O que
    dava para estacionar em quarentena cresceu cinco vezes sem ninguém decidir.
    As contagens ficam, e agora existe também um teto de bytes.
    """
    assert ADMIN_URL and RUNTIME_URL
    admin, seller, product, course, module, lesson = _personas()
    manifest, _ = load_manifest()
    # O `asset_id` precisa existir em `asset_record`: reaproveita o da aula do
    # manifesto, como o teste de cota por unidade já faz.
    asset = UUID(manifest["lessons"][0]["asset_id"])
    meio_giga = 512 * 1024 * 1024
    with psycopg.connect(ADMIN_URL) as connection:
        # Oito envios no tamanho máximo enchem os 4 GiB em voo, e ainda ficam
        # bem abaixo das cem unidades que a outra cota permite.
        connection.execute(
            """
            insert into app_private.asset_upload(
              seller_organization_id,id,identity_user_id,catalog_product_id,course_id,
              module_id,lesson_id,asset_id,blob_id,reserved_version,title,file_name,
              expected_media_type,expected_byte_size,expected_sha256,storage_driver,
              quarantine_bucket,ready_bucket,quarantine_key,ready_key,status,expires_at
            ) select %s,gen_random_uuid(),%s,%s,%s,%s,%s,%s,
              gen_random_uuid(),ordinality+500,'Em voo','aula-em-voo.mp4','video/mp4',%s,
              encode(digest('voo'||ordinality::text,'sha256'),'hex'),'s3',
              'appsell-quarantine','appsell-ready','voo-quarantine-'||ordinality,
              'voo-ready-'||ordinality,'pending',clock_timestamp()+interval '30 minutes'
            from generate_series(1,8) input(ordinality)
            """,
            (seller, admin, product, course, module, lesson, asset, meio_giga),
        )
        connection.execute(
            """
            insert into app_private.asset_storage_cleanup_job(
              seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
              storage_version_id,status,available_at
            ) select seller_organization_id,id,'quarantine_reconcile','s3',
              quarantine_bucket,quarantine_key,null,'queued',expires_at
            from app_private.asset_upload
            where seller_organization_id=%s and title='Em voo'
            """,
            (seller,),
        )
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection, pytest.raises(psycopg.DatabaseError) as recusa:
        _intencao_de_envio(
            connection,
            admin=admin,
            seller=seller,
            product=product,
            course=course,
            module=module,
            lesson=lesson,
            byte_size=1024,
        )
    assert "open asset upload byte quota" in str(recusa.value)
