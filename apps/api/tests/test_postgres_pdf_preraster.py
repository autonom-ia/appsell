from __future__ import annotations

import json
import os
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_postgres_pdf_access_policy import (
    BUYER,
    OTHER_OWNER,
    OWNER,
    add_asset,
    apply_context,
    create_graph,
    digest,
    seed_entitlement,
    seed_identities,
    seed_legacy_publication,
)

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

WORKER = UUID("82000000-0000-4000-8000-000000000001")


@pytest.fixture(scope="module", autouse=True)
def identities() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seed_identities(connection)
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,'https://identity.pdf-preraster.test','pdf-preraster-worker',
              'pdf-preraster-worker@example.test',true)
            on conflict(id) do nothing
            """,
            (WORKER,),
        )
        connection.execute(
            """
            insert into app_private.asset_worker_identity(identity_user_id,status,label)
            values(%s,'active','pdf-preraster-test-worker')
            on conflict(identity_user_id) do update set status='active'
            """,
            (WORKER,),
        )
        connection.commit()


def _page(
    seller: UUID, asset: UUID, version: int, page_number: int, *, size: int = 4096
) -> dict[str, Any]:
    return {
        "page_number": page_number,
        "storage_driver": "s3",
        "storage_bucket": "appsell-ready",
        "object_key": (f"tenants/{seller}/assets/{asset}/v{version}/pages/{page_number}.png"),
        "storage_version_id": f"page-version-{page_number}",
        "storage_etag": f"page-etag-{page_number}",
        "byte_size": size,
        "sha256": digest(f"page-{seller}-{asset}-{version}-{page_number}"),
        "pixel_width": 1200,
        "pixel_height": 1600,
    }


def test_schema_contract_is_forced_rls_append_only_and_tenant_keyed() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        schema = connection.execute(
            """
            select app_private.schema_ready(),
              (select version_num from alembic_version),
              (select relrowsecurity and relforcerowsecurity from pg_class
                where oid='public.asset_pdf_raster'::regclass),
              (select relrowsecurity and relforcerowsecurity from pg_class
                where oid='public.asset_pdf_page'::regclass),
              to_regprocedure(
                'app_private.register_asset_ready_candidate_v2(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb)'
              ) is not null,
              to_regprocedure(
                'app_private.finalize_asset_verification_v2(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid,text)'
              ) is not null
            """
        ).fetchone()
        assert schema == (True, "0043_material_role_by_media", True, True, True, True)
        primary_keys = connection.execute(
            """
            select c.relname,pg_get_constraintdef(k.oid)
            from pg_constraint k join pg_class c on c.oid=k.conrelid
            where k.contype='p' and c.relname in ('asset_pdf_raster','asset_pdf_page')
            order by c.relname
            """
        ).fetchall()
        assert primary_keys == [
            (
                "asset_pdf_page",
                "PRIMARY KEY (seller_organization_id, asset_id, asset_version, page_number)",
            ),
            (
                "asset_pdf_raster",
                "PRIMARY KEY (seller_organization_id, asset_id, asset_version)",
            ),
        ]
        triggers = connection.execute(
            """
            select tgname from pg_trigger
            where tgrelid in (
              'public.asset_pdf_raster'::regclass,'public.asset_pdf_page'::regclass
            ) and not tgisinternal and tgenabled='O' order by tgname
            """
        ).fetchall()
        assert triggers == [
            ("asset_pdf_page_append_only",),
            ("asset_pdf_page_limits_valid",),
            ("asset_pdf_raster_append_only",),
            ("asset_pdf_raster_source_valid",),
        ]


def test_pdf_candidate_requires_complete_canonical_manifest_and_finalizes_atomically() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection)
        asset = add_asset(connection, graph, media_type="application/pdf", position=0)
        upload = uuid4()
        blob = uuid4()
        source_sha = digest(f"source-{upload}")
        ready_sha = digest(f"ready-{upload}")
        connection.execute(
            """
            insert into app_private.asset_upload(
              seller_organization_id,id,identity_user_id,catalog_product_id,course_id,
              module_id,lesson_id,asset_id,blob_id,reserved_version,title,file_name,
              expected_media_type,expected_byte_size,expected_sha256,storage_driver,
              quarantine_bucket,ready_bucket,quarantine_key,ready_key,observed_version_id,
              observed_etag,status,expires_at
            ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,2,'Raster PDF','raster.pdf',
              'application/pdf',8192,%s,'s3','appsell-quarantine','appsell-ready',
              %s,%s,'source-version','source-etag','verifying',
              clock_timestamp()+interval '1 hour')
            """,
            (
                graph.seller,
                upload,
                OWNER,
                graph.product,
                graph.course,
                graph.module,
                graph.lesson,
                asset.asset,
                blob,
                source_sha,
                f"tenants/{graph.seller}/quarantine/{upload}.pdf",
                f"tenants/{graph.seller}/assets/{asset.asset}/v2/original.pdf",
            ),
        )
        connection.execute(
            """
            insert into app_private.asset_verification_job(
              seller_organization_id,upload_id,status,attempts,worker_id,lease_expires_at
            ) values(%s,%s,'leased',1,'raster-worker',clock_timestamp()+interval '5 minutes')
            """,
            (graph.seller, upload),
        )
        connection.commit()

    pages = [_page(graph.seller, asset.asset, 2, page) for page in (1, 2, 3)]
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        with pytest.raises(psycopg.DatabaseError) as stale_lease:
            connection.execute(
                """
                select app_private.register_asset_ready_candidate_v2(
                  %s,%s,%s,'stale-worker','stale-ready-version','stale-ready-etag',8192,%s,
                  'application/pdf','application/pdf','clean',%s)
                """,
                (WORKER, graph.seller, upload, ready_sha, Jsonb(pages)),
            )
        assert stale_lease.value.sqlstate == "P4091"
        connection.rollback()
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                select app_private.register_asset_ready_candidate_v2(
                  %s,%s,%s,'raster-worker','ready-version','ready-etag',8192,%s,
                  'application/pdf','application/pdf','clean',%s)
                """,
                (
                    WORKER,
                    graph.seller,
                    upload,
                    ready_sha,
                    Jsonb([pages[0], pages[2]]),
                ),
            )
        connection.rollback()
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                select app_private.register_asset_ready_candidate(
                  %s,%s,%s,'raster-worker','ready-version','ready-etag',8192,%s,
                  'application/pdf','application/pdf','clean')
                """,
                (WORKER, graph.seller, upload, ready_sha),
            )
        connection.rollback()
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        registered_row = connection.execute(
            """
            select app_private.register_asset_ready_candidate_v2(
              %s,%s,%s,'raster-worker','ready-version','ready-etag',8192,%s,
              'application/pdf','application/pdf','clean',%s)
            """,
            (WORKER, graph.seller, upload, ready_sha, Jsonb(pages)),
        ).fetchone()
        assert registered_row is not None
        registered = cast(dict[str, Any], registered_row[0])
        connection.commit()
        assert registered["pdf_page_count"] == 3
        assert registered["pdf_total_byte_size"] == 12288
        manifest_sha = cast(str, registered["pdf_manifest_sha256"])

        apply_context(connection, WORKER, None, "catalog.asset.verify")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                select app_private.finalize_asset_verification(
                  %s,%s,%s,'raster-worker',8192,%s,8192,%s,
                  'application/pdf','application/pdf','clean','ready-version','ready-etag',
                  'ready',%s,%s,%s)
                """,
                (
                    WORKER,
                    graph.seller,
                    upload,
                    source_sha,
                    ready_sha,
                    uuid4(),
                    digest("old-finalize"),
                    uuid4(),
                ),
            )
        connection.rollback()
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        finalized_row = connection.execute(
            """
            select app_private.finalize_asset_verification_v2(
              %s,%s,%s,'raster-worker',8192,%s,8192,%s,
              'application/pdf','application/pdf','clean','ready-version','ready-etag',
              'ready',%s,%s,%s,%s)
            """,
            (
                WORKER,
                graph.seller,
                upload,
                source_sha,
                ready_sha,
                uuid4(),
                digest("v2-finalize"),
                uuid4(),
                manifest_sha,
            ),
        ).fetchone()
        assert finalized_row is not None
        connection.commit()
        finalized = cast(dict[str, Any], finalized_row[0])
        assert finalized["ready"] is True
        assert finalized["pdf_page_count"] == 3

    with psycopg.connect(ADMIN_URL) as connection:
        raster = connection.execute(
            """
            select page_count,total_byte_size,manifest_sha256 from asset_pdf_raster
            where seller_organization_id=%s and asset_id=%s and asset_version=2
            """,
            (graph.seller, asset.asset),
        ).fetchone()
        assert raster == (3, 12288, manifest_sha)
        assert connection.execute(
            """
            select count(*),min(page_number),max(page_number),sum(byte_size)
            from asset_pdf_page where seller_organization_id=%s and asset_id=%s
              and asset_version=2
            """,
            (graph.seller, asset.asset),
        ).fetchone() == (3, 1, 3, 12288)
        assert connection.execute(
            """
            select count(*) from app_private.asset_storage_cleanup_job
            where seller_organization_id=%s and upload_id=%s
              and kind='pdf_page_orphan' and status='protected'
            """,
            (graph.seller, upload),
        ).fetchone() == (3,)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update asset_pdf_page set byte_size=1 where seller_organization_id=%s
                  and asset_id=%s and asset_version=2 and page_number=1
                """,
                (graph.seller, asset.asset),
            )
        connection.rollback()


def test_s3_pdf_publication_requires_complete_raster_before_insert() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection)
        legacy = add_asset(connection, graph, media_type="application/pdf", position=0)
        publication = seed_legacy_publication(connection, graph, legacy)
        asset_id, blob_id = uuid4(), uuid4()
        original_key = f"tenants/{graph.seller}/assets/{asset_id}/v1/original.pdf"
        connection.execute(
            "insert into asset_record(seller_organization_id,id,catalog_product_id,title) "
            "values(%s,%s,%s,'S3 PDF publication gate')",
            (graph.seller, asset_id, graph.product),
        )
        connection.execute(
            """
            insert into asset_blob(
              seller_organization_id,id,storage_driver,storage_bucket,object_key,
              storage_version_id,storage_etag,file_name,media_type,byte_size,sha256,
              magic_type,scanner_result,verified_at
            ) values(%s,%s,'s3','appsell-ready',%s,'original-version','original-etag',
              'publication-gate.pdf','application/pdf',8192,%s,'application/pdf',
              'clean',clock_timestamp())
            """,
            (graph.seller, blob_id, original_key, digest(original_key)),
        )
        connection.execute(
            """
            insert into asset_version(
              seller_organization_id,asset_id,version,blob_id,actor_identity_user_id
            ) values(%s,%s,1,%s,%s)
            """,
            (graph.seller, asset_id, blob_id, OWNER),
        )
        connection.commit()

        publication_insert = """
            insert into publication_asset(
              seller_organization_id,catalog_product_id,publication_id,course_id,
              module_id,lesson_id,asset_id,asset_version,blob_id,position,title,
              file_name,media_type,byte_size,sha256,storage_driver,storage_bucket,
              object_key,storage_version_id,storage_etag,content_role,download_allowed
            ) values(%s,%s,%s,%s,%s,%s,%s,1,%s,1,'S3 PDF publication gate',
              'publication-gate.pdf','application/pdf',8192,%s,'s3','appsell-ready',
              %s,'original-version','original-etag','course_content',false)
        """
        publication_parameters = (
            graph.seller,
            graph.product,
            publication,
            graph.course,
            graph.module,
            graph.lesson,
            asset_id,
            blob_id,
            digest(original_key),
            original_key,
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="pre-rasterized"):
            connection.execute(publication_insert, publication_parameters)
        connection.rollback()

        pages = [_page(graph.seller, asset_id, 1, page) for page in (1, 2)]
        connection.execute(
            """
            insert into asset_pdf_raster(
              seller_organization_id,asset_id,asset_version,blob_id,page_count,
              total_byte_size,manifest_sha256
            ) values(%s,%s,1,%s,2,8192,%s)
            """,
            (graph.seller, asset_id, blob_id, digest("publication-gate-manifest")),
        )
        first = pages[0]
        connection.execute(
            """
            insert into asset_pdf_page(
              seller_organization_id,asset_id,asset_version,page_number,storage_driver,
              storage_bucket,object_key,storage_version_id,storage_etag,byte_size,sha256,
              pixel_width,pixel_height
            ) values(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                graph.seller,
                asset_id,
                first["page_number"],
                first["storage_driver"],
                first["storage_bucket"],
                first["object_key"],
                first["storage_version_id"],
                first["storage_etag"],
                first["byte_size"],
                first["sha256"],
                first["pixel_width"],
                first["pixel_height"],
            ),
        )
        connection.commit()
        with pytest.raises(psycopg.errors.CheckViolation, match="pre-rasterized"):
            connection.execute(publication_insert, publication_parameters)
        connection.rollback()

        second = pages[1]
        connection.execute(
            """
            insert into asset_pdf_page(
              seller_organization_id,asset_id,asset_version,page_number,storage_driver,
              storage_bucket,object_key,storage_version_id,storage_etag,byte_size,sha256,
              pixel_width,pixel_height
            ) values(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                graph.seller,
                asset_id,
                second["page_number"],
                second["storage_driver"],
                second["storage_bucket"],
                second["object_key"],
                second["storage_version_id"],
                second["storage_etag"],
                second["byte_size"],
                second["sha256"],
                second["pixel_width"],
                second["pixel_height"],
            ),
        )
        connection.execute(publication_insert, publication_parameters)
        connection.commit()
        assert connection.execute(
            """
            select asset_id,asset_version from publication_asset
            where seller_organization_id=%s and publication_id=%s and asset_id=%s
            """,
            (graph.seller, publication, asset_id),
        ).fetchone() == (asset_id, 1)


def test_entitled_manifest_and_exact_page_are_tenant_scoped_without_original_locator() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection)
        asset = add_asset(connection, graph, media_type="application/pdf", position=0)
        publication = seed_legacy_publication(connection, graph, asset)
        connection.execute(
            "update catalog_product set status='published',published_snapshot_id=%s "
            "where seller_organization_id=%s and id=%s",
            (publication, graph.seller, graph.product),
        )
        pages = [_page(graph.seller, asset.asset, 1, page) for page in (1, 2)]
        canonical = json.dumps(pages, sort_keys=True, separators=(",", ":"))
        manifest_sha = digest(canonical)
        connection.execute(
            """
            insert into asset_pdf_raster(
              seller_organization_id,asset_id,asset_version,blob_id,page_count,
              total_byte_size,manifest_sha256
            ) values(%s,%s,1,%s,2,8192,%s)
            """,
            (graph.seller, asset.asset, asset.blob, manifest_sha),
        )
        for page in pages:
            connection.execute(
                """
                insert into asset_pdf_page(
                  seller_organization_id,asset_id,asset_version,page_number,
                  storage_driver,storage_bucket,object_key,storage_version_id,
                  storage_etag,byte_size,sha256,pixel_width,pixel_height
                ) values(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    graph.seller,
                    asset.asset,
                    page["page_number"],
                    page["storage_driver"],
                    page["storage_bucket"],
                    page["object_key"],
                    page["storage_version_id"],
                    page["storage_etag"],
                    page["byte_size"],
                    page["sha256"],
                    page["pixel_width"],
                    page["pixel_height"],
                ),
            )
        connection.commit()
        entitlement = seed_entitlement(graph, publication)

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "library.asset.read")
        manifest_cursor = connection.execute(
            "select * from app_private.authorize_entitled_pdf_manifest(%s,%s,%s)",
            (BUYER, entitlement, asset.asset),
        )
        manifest = manifest_cursor.fetchone()
        assert manifest is not None
        assert manifest_cursor.description is not None
        manifest_mapping = dict(
            zip(
                [item.name for item in manifest_cursor.description],
                manifest,
                strict=True,
            )
        )
        assert manifest_mapping["seller_organization_id"] == graph.seller
        assert manifest_mapping["blob_id"] == asset.blob
        assert manifest_mapping["page_count"] == 2
        serialized_manifest = json.dumps(manifest_mapping, default=str)
        assert "object_key" not in serialized_manifest
        assert "storage_bucket" not in serialized_manifest
        assert "policy-" not in serialized_manifest

        apply_context(connection, BUYER, None, "library.asset.read")
        page_row = connection.execute(
            "select * from app_private.authorize_entitled_pdf_page(%s,%s,%s,2)",
            (BUYER, entitlement, asset.asset),
        ).fetchone()
        assert page_row is not None
        assert page_row[0] == graph.seller and page_row[1] == asset.asset
        assert page_row[5] == 2 and page_row[6] == "s3"
        assert page_row[8] == pages[1]["object_key"]
        assert page_row[13:15] == (1200, 1600)
        assert asset.file_name not in json.dumps(page_row, default=str)

        apply_context(connection, BUYER, None, "library.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_entitled_pdf_page(%s,%s,%s,3)",
                (BUYER, entitlement, asset.asset),
            )
        connection.rollback()


def test_pdf_study_is_private_idempotent_and_pinned_to_asset_version() -> None:
    assert ADMIN_URL and RUNTIME_URL
    other_buyer = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection)
        asset = add_asset(connection, graph, media_type="application/pdf", position=0)
        publication = seed_legacy_publication(connection, graph, asset)
        connection.execute(
            "update catalog_product set status='published',published_snapshot_id=%s "
            "where seller_organization_id=%s and id=%s",
            (publication, graph.seller, graph.product),
        )
        pages = [_page(graph.seller, asset.asset, 1, page) for page in (1, 2)]
        canonical = json.dumps(pages, sort_keys=True, separators=(",", ":"))
        connection.execute(
            """
            insert into asset_pdf_raster(
              seller_organization_id,asset_id,asset_version,blob_id,page_count,
              total_byte_size,manifest_sha256
            ) values(%s,%s,1,%s,2,8192,%s)
            """,
            (graph.seller, asset.asset, asset.blob, digest(canonical)),
        )
        for page in pages:
            connection.execute(
                """
                insert into asset_pdf_page(
                  seller_organization_id,asset_id,asset_version,page_number,
                  storage_driver,storage_bucket,object_key,storage_version_id,
                  storage_etag,byte_size,sha256,pixel_width,pixel_height
                ) values(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    graph.seller,
                    asset.asset,
                    page["page_number"],
                    page["storage_driver"],
                    page["storage_bucket"],
                    page["object_key"],
                    page["storage_version_id"],
                    page["storage_etag"],
                    page["byte_size"],
                    page["sha256"],
                    page["pixel_width"],
                    page["pixel_height"],
                ),
            )
        connection.commit()
        entitlement = seed_entitlement(graph, publication)
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,'https://identity.pdf-study.test',%s,%s,true)
            """,
            (other_buyer, f"other-{other_buyer}", f"other-{other_buyer}@example.test"),
        )
        other_entitlement = uuid4()
        connection.execute(
            """
            insert into buyer_entitlement(
              seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
              publication_id,reference_key,source,status
            ) values(%s,%s,%s,%s,%s,%s,'manual','active')
            """,
            (
                graph.seller,
                other_entitlement,
                other_buyer,
                graph.product,
                publication,
                f"pdf-study-{other_entitlement}",
            ),
        )
        foreign_graph = create_graph(connection, owner=OTHER_OWNER, staff=False)
        foreign_asset = add_asset(
            connection, foreign_graph, media_type="application/pdf", position=0
        )
        foreign_publication = seed_legacy_publication(connection, foreign_graph, foreign_asset)
        connection.execute(
            "update catalog_product set status='published',published_snapshot_id=%s "
            "where seller_organization_id=%s and id=%s",
            (foreign_publication, foreign_graph.seller, foreign_graph.product),
        )
        foreign_page = _page(foreign_graph.seller, foreign_asset.asset, 1, 1)
        connection.execute(
            """
            insert into asset_pdf_raster(
              seller_organization_id,asset_id,asset_version,blob_id,page_count,
              total_byte_size,manifest_sha256
            ) values(%s,%s,1,%s,1,4096,%s)
            """,
            (
                foreign_graph.seller,
                foreign_asset.asset,
                foreign_asset.blob,
                digest(json.dumps([foreign_page], sort_keys=True, separators=(",", ":"))),
            ),
        )
        connection.execute(
            """
            insert into asset_pdf_page(
              seller_organization_id,asset_id,asset_version,page_number,
              storage_driver,storage_bucket,object_key,storage_version_id,
              storage_etag,byte_size,sha256,pixel_width,pixel_height
            ) values(%s,%s,1,1,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                foreign_graph.seller,
                foreign_asset.asset,
                foreign_page["storage_driver"],
                foreign_page["storage_bucket"],
                foreign_page["object_key"],
                foreign_page["storage_version_id"],
                foreign_page["storage_etag"],
                foreign_page["byte_size"],
                foreign_page["sha256"],
                foreign_page["pixel_width"],
                foreign_page["pixel_height"],
            ),
        )
        connection.commit()

    annotation_id = uuid4()
    idempotency = uuid4()
    request_id = uuid4()
    mutation = {
        "action": "annotation.upsert",
        "annotation": {
            "id": str(annotation_id),
            "page": 2,
            "tool": "highlight",
            "color": "#E59819",
            "width": 0.022,
            "points": [{"x": 0.1, "y": 0.2}, {"x": 0.8, "y": 0.2}],
        },
    }
    body_sha = digest(json.dumps(mutation, sort_keys=True))
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "learning.pdf-study.read")
        empty_row = connection.execute(
            "select app_private.read_pdf_study_state(%s,%s,%s)",
            (BUYER, entitlement, asset.asset),
        ).fetchone()
        assert empty_row is not None
        empty = cast(dict[str, Any], empty_row[0])
        assert empty["asset_version"] == 1
        assert empty["revision"] == 0
        assert empty["annotations"] == [] and empty["bookmarks"] == []

        apply_context(connection, BUYER, None, "learning.pdf-study.write")
        first_row = connection.execute(
            """
            select app_private.mutate_pdf_study_state(
              %s,%s,%s,0,%s,%s,%s,%s
            )
            """,
            (
                BUYER,
                entitlement,
                asset.asset,
                idempotency,
                body_sha,
                request_id,
                Jsonb(mutation),
            ),
        ).fetchone()
        assert first_row is not None
        first = cast(dict[str, Any], first_row[0])
        assert first["revision"] == 1
        assert first["annotations"][0]["id"] == str(annotation_id)

        apply_context(connection, BUYER, None, "learning.pdf-study.write")
        replay_row = connection.execute(
            "select app_private.mutate_pdf_study_state(%s,%s,%s,0,%s,%s,%s,%s)",
            (BUYER, entitlement, asset.asset, idempotency, body_sha, request_id, Jsonb(mutation)),
        ).fetchone()
        assert replay_row is not None and replay_row[0] == first
        connection.commit()

        apply_context(connection, BUYER, None, "learning.pdf-study.write")
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                "select app_private.mutate_pdf_study_state(%s,%s,%s,0,%s,%s,%s,%s)",
                (
                    BUYER,
                    entitlement,
                    asset.asset,
                    uuid4(),
                    digest("conflict"),
                    uuid4(),
                    Jsonb({"action": "bookmark.set", "page": 1, "active": True}),
                ),
            )
        connection.rollback()

        apply_context(connection, other_buyer, None, "learning.pdf-study.read")
        other_row = connection.execute(
            "select app_private.read_pdf_study_state(%s,%s,%s)",
            (other_buyer, other_entitlement, asset.asset),
        ).fetchone()
        assert other_row is not None
        other = cast(dict[str, Any], other_row[0])
        assert other["annotations"] == [] and other["bookmarks"] == []

        apply_context(connection, other_buyer, None, "learning.pdf-study.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_pdf_study_state(%s,%s,%s)",
                (other_buyer, entitlement, asset.asset),
            )
        connection.rollback()

        apply_context(connection, other_buyer, None, "learning.pdf-study.write")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.mutate_pdf_study_state(%s,%s,%s,0,%s,%s,%s,%s)",
                (
                    other_buyer,
                    entitlement,
                    asset.asset,
                    uuid4(),
                    digest("other-buyer-write"),
                    uuid4(),
                    Jsonb({"action": "bookmark.set", "page": 1, "active": True}),
                ),
            )
        connection.rollback()

        apply_context(connection, BUYER, None, "learning.pdf-study.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_pdf_study_state(%s,%s,%s)",
                (BUYER, entitlement, uuid4()),
            )
        connection.rollback()

        apply_context(connection, BUYER, None, "learning.pdf-study.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_pdf_study_state(%s,%s,%s)",
                (BUYER, entitlement, foreign_asset.asset),
            )
        connection.rollback()

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select * from app_private.learning_pdf_annotation")
        connection.rollback()

    with psycopg.connect(ADMIN_URL) as connection:
        blob_v2 = uuid4()
        connection.execute(
            """
            insert into asset_blob(
              seller_organization_id,id,storage_driver,storage_bucket,object_key,
              file_name,media_type,byte_size,sha256,magic_type,scanner_result,verified_at
            ) values(%s,%s,'legacy_bundle','legacy-bundle',%s,'study-v2.pdf',
              'application/pdf',100,%s,'application/pdf','legacy_trusted',clock_timestamp())
            """,
            (graph.seller, blob_v2, f"legacy/pdf-study/{blob_v2}.pdf", digest(str(blob_v2))),
        )
        connection.execute(
            """
            insert into asset_version(
              seller_organization_id,asset_id,version,blob_id,actor_identity_user_id
            ) values(%s,%s,2,%s,%s)
            """,
            (graph.seller, asset.asset, blob_v2, OWNER),
        )
        connection.execute(
            """
            insert into app_private.learning_pdf_study_state(
              seller_organization_id,buyer_identity_user_id,entitlement_id,
              asset_id,asset_version,revision
            ) values(%s,%s,%s,%s,2,9)
            """,
            (graph.seller, BUYER, entitlement, asset.asset),
        )
        connection.execute(
            """
            insert into app_private.learning_pdf_bookmark(
              seller_organization_id,buyer_identity_user_id,entitlement_id,
              asset_id,asset_version,page_number
            ) values(%s,%s,%s,%s,2,1)
            """,
            (graph.seller, BUYER, entitlement, asset.asset),
        )
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "learning.pdf-study.read")
        current_row = connection.execute(
            "select app_private.read_pdf_study_state(%s,%s,%s)",
            (BUYER, entitlement, asset.asset),
        ).fetchone()
        assert current_row is not None
        current = cast(dict[str, Any], current_row[0])
        assert current["asset_version"] == 1
        assert current["revision"] == 1
        assert current["bookmarks"] == []
