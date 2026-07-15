from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

DEMO_KEY = "autonomia-demo-v1"
DEMO_DATABASE_MARKER = "appsell-demo-reset-target-v1"
ASSET_WORKER_SUBJECT = "asset-worker-v1"
ASSET_WORKER_EMAIL = "asset.worker@autonomia.site"
DEMO_SUPERADMIN_SUBJECT = "demo-superadmin"
DEMO_SUPERADMIN_EMAIL = "comercial@autonomia.site"
EXPECTED_MIGRATION = "0014_multi_asset_player"
LIBPQ_REDIRECT_ENVIRONMENT = (
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGSYSCONFDIR",
)
ROOT = Path(__file__).resolve().parents[4]
MANIFEST_PATH = ROOT / "demo" / "content" / "codex-na-pratica" / "manifest.json"
PDF_ROOT = ROOT / "output" / "pdf" / "codex"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset the isolated AppSell demo tenant.")
    parser.add_argument("--confirm-demo-key", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prepare-storage-cleanup", action="store_true")
    return parser.parse_args()


def load_manifest() -> tuple[dict[str, Any], str]:
    raw = MANIFEST_PATH.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("seed_key") != DEMO_KEY or manifest.get("version") != 1:
        raise RuntimeError("unsupported demo manifest")
    personas = manifest.get("personas", {})
    expected = {
        "admin": ("demo-admin-v1", "admin.demo@autonomia.site"),
        "buyer": ("demo-user-v1", "user.demo@autonomia.site"),
    }
    for role, (subject, email) in expected.items():
        persona = personas.get(role, {})
        if persona.get("subject") != subject or persona.get("email") != email:
            raise RuntimeError(f"invalid {role} demo persona")
    if personas["admin"]["subject"] == personas["buyer"]["subject"]:
        raise RuntimeError("demo personas must be distinct")
    lessons = manifest.get("lessons")
    if not isinstance(lessons, list) or len(lessons) != 5:
        raise RuntimeError("demo course must contain exactly five lessons")
    for position, lesson in enumerate(lessons):
        if lesson.get("position") != position:
            raise RuntimeError("demo lesson positions must be contiguous")
        file_name = f"{lesson['slug']}.pdf"
        path = PDF_ROOT / file_name
        payload = path.read_bytes()
        if len(payload) != lesson.get("bytes"):
            raise RuntimeError(f"demo PDF size mismatch: {file_name}")
        if hashlib.sha256(payload).hexdigest() != lesson.get("sha256"):
            raise RuntimeError(f"demo PDF hash mismatch: {file_name}")
    video = manifest.get("video")
    expected_video_name = f"{video.get('slug')}.mp4" if isinstance(video, dict) else ""
    if (
        not isinstance(video, dict)
        or video.get("lesson_id") != lessons[-1].get("lesson_id")
        or video.get("file_name") != expected_video_name
        or video.get("position") != 0
        or not isinstance(video.get("bytes"), int)
        or not 1 <= video["bytes"] <= 100 * 1024 * 1024
        or not isinstance(video.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", video["sha256"])
    ):
        raise RuntimeError("invalid demo video manifest")
    video_path = PDF_ROOT / video["file_name"]
    video_payload = video_path.read_bytes()
    if len(video_payload) != video["bytes"]:
        raise RuntimeError(f"demo video size mismatch: {video['file_name']}")
    if hashlib.sha256(video_payload).hexdigest() != video["sha256"]:
        raise RuntimeError(f"demo video hash mismatch: {video['file_name']}")
    return manifest, hashlib.sha256(raw).hexdigest()


def identity_id(issuer: str, subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{issuer}\0{subject}")


def require_environment(args: argparse.Namespace) -> tuple[str, str, bool]:
    if args.confirm_demo_key != DEMO_KEY:
        raise RuntimeError("demo confirmation key mismatch")
    node_environment = os.environ.get("NODE_ENV", "").strip().lower()
    if node_environment not in {"development", "test"}:
        raise RuntimeError("NODE_ENV must explicitly be development or test")
    app_environment = os.environ.get("APP_ENV", "").strip().lower()
    if app_environment not in {"local", "ci"}:
        raise RuntimeError("APP_ENV must explicitly be local or ci")
    if os.environ.get("APPSELL_DEMO_ENABLED", "").lower() != "true":
        raise RuntimeError("APPSELL_DEMO_ENABLED must be true")
    environment = os.environ.get("APPSELL_API_ENVIRONMENT", "").lower()
    if environment not in {"local", "ci"}:
        raise RuntimeError("APPSELL_API_ENVIRONMENT must explicitly be local or ci")
    issuer = os.environ.get("APPSELL_API_IDENTITY_ISSUER", "").strip()
    if not issuer or any(ord(character) < 0x20 for character in issuer):
        raise RuntimeError("APPSELL_API_IDENTITY_ISSUER is required")
    database_url = os.environ.get("APPSELL_DEMO_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("APPSELL_DEMO_DATABASE_URL must be a PostgreSQL DSN")
    if any(os.environ.get(name) for name in LIBPQ_REDIRECT_ENVIRONMENT):
        raise RuntimeError("libpq network and service environment overrides are forbidden")
    connection_info = conninfo_to_dict(database_url)
    if connection_info.get("host") not in {"127.0.0.1", "localhost", "::1"} or connection_info.get(
        "hostaddr"
    ):
        raise RuntimeError("demo reset database must be loopback-only")
    superadmin_value = os.environ.get("APPSELL_DEMO_SUPERADMIN_ENABLED", "").strip().lower()
    if superadmin_value not in {"", "false", "true"}:
        raise RuntimeError("APPSELL_DEMO_SUPERADMIN_ENABLED must explicitly be true or false")
    return issuer, database_url, superadmin_value == "true"


def validate_demo_database(connection: psycopg.Connection[Any]) -> None:
    marker = connection.execute(
        "select shobj_description(oid,'pg_database') from pg_database "
        "where datname=current_database()"
    ).fetchone()
    if marker != (DEMO_DATABASE_MARKER,):
        raise RuntimeError("database is not marked as an AppSell demo reset target")


def snapshot_assets(manifest: dict[str, Any], lesson: dict[str, Any]) -> list[dict[str, Any]]:
    assets = [
        {
            "id": lesson["asset_id"],
            "version": 1,
            "title": lesson["title"],
            "file_name": f"{lesson['slug']}.pdf",
            "media_type": "application/pdf",
            "byte_size": lesson["bytes"],
            "sha256": lesson["sha256"],
            "position": lesson["position"],
        }
    ]
    video = manifest.get("video")
    if isinstance(video, dict) and video.get("lesson_id") == lesson["lesson_id"]:
        assets.insert(
            0,
            {
                "id": video["asset_id"],
                "version": 1,
                "title": video["title"],
                "file_name": video["file_name"],
                "media_type": "video/mp4",
                "byte_size": video["bytes"],
                "sha256": video["sha256"],
                "position": video["position"],
            },
        )
    return assets


def snapshot_for(manifest: dict[str, Any]) -> dict[str, Any]:
    seller = manifest["seller"]
    product = manifest["product"]
    course = manifest["course"]
    module = manifest["module"]
    offer = manifest["offer"]
    lessons = manifest["lessons"]
    return {
        "schema_version": 3,
        "seller_organization_id": seller["id"],
        "product": {
            "id": product["id"],
            "slug": product["slug"],
            "title": product["title"],
            "description": product["description"],
        },
        "offer": {
            "id": offer["id"],
            "title": offer["title"],
            "summary": offer["summary"],
            "billing_authority": "external_unconfigured",
        },
        "courses": [
            {
                "id": course["id"],
                "title": course["title"],
                "description": course["description"],
                "position": 0,
                "modules": [
                    {
                        "id": module["id"],
                        "title": module["title"],
                        "status": "active",
                        "position": 0,
                        "lessons": [
                            {
                                "id": lesson["lesson_id"],
                                "title": lesson["title"],
                                "kind": "text",
                                "content": lesson["summary"],
                                "status": "active",
                                "position": lesson["position"],
                                "assets": snapshot_assets(manifest, lesson),
                            }
                            for lesson in lessons
                        ],
                    }
                ],
            }
        ],
    }


def prepare_demo_storage_cleanup(
    connection: psycopg.Connection[Any], manifest: dict[str, Any]
) -> int:
    seller_id = UUID(manifest["seller"]["id"])
    connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (DEMO_KEY,))
    connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"appsell.asset/reset/{seller_id}",),
    )
    connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"appsell.asset/quota/{seller_id}",),
    )
    validate_demo_database(connection)
    connection.execute("select set_config('appsell.demo_reset_key',%s,true)", (DEMO_KEY,))
    version = connection.execute("select version_num from alembic_version").fetchone()
    if version != (EXPECTED_MIGRATION,):
        raise RuntimeError(f"database must be migrated to {EXPECTED_MIGRATION}")

    connection.execute(
        """
        update app_private.asset_upload set status='cancelled',
          failure_code='demo_reset_requested',updated_at=clock_timestamp()
        where seller_organization_id=%s and status in ('pending','uploaded','verifying')
        """,
        (seller_id,),
    )
    connection.execute(
        """
        update app_private.asset_verification_job verification set status='failed',
          lease_expires_at=null,updated_at=clock_timestamp()
        where verification.seller_organization_id=%s
          and verification.status in ('queued','leased')
        """,
        (seller_id,),
    )
    connection.execute(
        """
        update app_private.asset_storage_cleanup_job
        set status='queued',attempts=0,worker_id=null,lease_expires_at=null,
          available_at=clock_timestamp(),failure_code=null,updated_at=clock_timestamp()
        where seller_organization_id=%s and status<>'completed'
        """,
        (seller_id,),
    )
    connection.execute(
        """
        update app_private.asset_storage_cleanup_job cleanup
        set status='queued',attempts=0,worker_id=null,lease_expires_at=null,
          available_at=clock_timestamp(),failure_code=null,updated_at=clock_timestamp()
        from app_private.asset_upload upload, asset_blob blob
        where upload.seller_organization_id=%s
          and blob.seller_organization_id=upload.seller_organization_id
          and blob.id=upload.blob_id and blob.storage_driver='s3'
          and cleanup.seller_organization_id=upload.seller_organization_id
          and cleanup.upload_id=upload.id and cleanup.kind='ready_orphan'
          and cleanup.storage_driver=blob.storage_driver
          and cleanup.bucket=blob.storage_bucket and cleanup.object_key=blob.object_key
          and cleanup.storage_version_id=blob.storage_version_id
        """,
        (seller_id,),
    )
    connection.execute(
        """
        insert into app_private.asset_storage_cleanup_job(
          seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
          storage_version_id,storage_etag,byte_size,sha256,media_type,status,available_at
        )
        select upload.seller_organization_id,upload.id,'ready_orphan',blob.storage_driver,
          blob.storage_bucket,blob.object_key,blob.storage_version_id,blob.storage_etag,
          blob.byte_size,blob.sha256,blob.media_type,'queued',clock_timestamp()
        from app_private.asset_upload upload
        join asset_blob blob on blob.seller_organization_id=upload.seller_organization_id
          and blob.id=upload.blob_id and blob.storage_driver='s3'
        where upload.seller_organization_id=%s and not exists(
          select 1 from app_private.asset_storage_cleanup_job cleanup
          where cleanup.seller_organization_id=upload.seller_organization_id
            and cleanup.upload_id=upload.id and cleanup.kind='ready_orphan'
            and cleanup.storage_driver=blob.storage_driver
            and cleanup.bucket=blob.storage_bucket and cleanup.object_key=blob.object_key
            and cleanup.storage_version_id=blob.storage_version_id
        )
        """,
        (seller_id,),
    )
    connection.execute(
        """
        update app_private.asset_storage_cleanup_job
        set status='queued',attempts=0,worker_id=null,lease_expires_at=null,
          available_at=clock_timestamp(),failure_code=null,updated_at=clock_timestamp()
        where seller_organization_id=%s
          and kind in ('quarantine_reconcile','ready_reconcile')
        """,
        (seller_id,),
    )
    pending = connection.execute(
        "select count(*) from app_private.asset_storage_cleanup_job "
        "where seller_organization_id=%s and status<>'completed'",
        (seller_id,),
    ).fetchone()
    if pending is None:
        raise RuntimeError("demo cleanup count is unavailable")
    return int(pending[0])


def reset_demo(
    connection: psycopg.Connection[Any],
    manifest: dict[str, Any],
    manifest_sha: str,
    issuer: str,
    *,
    provision_superadmin: bool = False,
) -> None:
    seller = manifest["seller"]
    product = manifest["product"]
    course = manifest["course"]
    module = manifest["module"]
    offer = manifest["offer"]
    publication = manifest["publication"]
    entitlement = manifest["entitlement"]
    lessons = manifest["lessons"]
    admin = manifest["personas"]["admin"]
    buyer = manifest["personas"]["buyer"]
    admin_id = identity_id(issuer, admin["subject"])
    buyer_id = identity_id(issuer, buyer["subject"])
    superadmin_id = identity_id(issuer, DEMO_SUPERADMIN_SUBJECT)
    asset_worker_id = identity_id(issuer, ASSET_WORKER_SUBJECT)
    seller_id = UUID(seller["id"])
    product_id = UUID(product["id"])
    course_id = UUID(course["id"])
    module_id = UUID(module["id"])
    offer_id = UUID(offer["id"])
    publication_id = UUID(publication["id"])
    entitlement_id = UUID(entitlement["id"])
    lesson_ids = [UUID(lesson["lesson_id"]) for lesson in lessons]

    connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (DEMO_KEY,))
    connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"appsell.asset/reset/{seller_id}",),
    )
    connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"appsell.asset/quota/{seller_id}",),
    )
    validate_demo_database(connection)
    connection.execute("select set_config('appsell.demo_reset_key',%s,true)", (DEMO_KEY,))
    version = connection.execute("select version_num from alembic_version").fetchone()
    if version != (EXPECTED_MIGRATION,):
        raise RuntimeError(f"database must be migrated to {EXPECTED_MIGRATION}")
    unsafe_storage = connection.execute(
        """
        select exists(
          select 1 from asset_blob blob
          left join app_private.asset_upload upload
            on upload.seller_organization_id=blob.seller_organization_id
           and upload.blob_id=blob.id
          where blob.seller_organization_id=%s and blob.storage_driver='s3'
            and (upload.id is null or not exists(
              select 1 from app_private.asset_storage_cleanup_job cleanup
              where cleanup.seller_organization_id=upload.seller_organization_id
                and cleanup.upload_id=upload.id and cleanup.kind='ready_orphan'
                and cleanup.storage_driver=blob.storage_driver
                and cleanup.bucket=blob.storage_bucket
                and cleanup.object_key=blob.object_key
                and cleanup.storage_version_id=blob.storage_version_id
                and cleanup.status='completed'
            ))
        ) or exists(
          select 1 from app_private.asset_storage_cleanup_job
          where seller_organization_id=%s and status<>'completed'
        ) or exists(
          select 1 from app_private.asset_upload upload
          where upload.seller_organization_id=%s and not exists(
            select 1 from app_private.asset_storage_cleanup_job reconcile
            where reconcile.seller_organization_id=upload.seller_organization_id
              and reconcile.upload_id=upload.id and reconcile.kind='quarantine_reconcile'
              and reconcile.status='completed'
          )
        )
        """,
        (seller_id, seller_id, seller_id),
    ).fetchone()
    if unsafe_storage != (False,):
        raise RuntimeError("demo reset blocked until private S3 storage cleanup is drained")
    revoked_inspections = connection.execute(
        """
        update app_private.demo_inspection_ticket set
          state='revoked',revoked_at=clock_timestamp(),
          revoked_reason='Revoked by isolated demo tenant reset'
        where seller_organization_id=%s and state='active'
        returning id,actor_identity_user_id
        """,
        (seller_id,),
    ).fetchall()
    for inspection_id, actor_identity_user_id in revoked_inspections:
        connection.execute(
            """
            insert into audit_event(
              seller_organization_id,actor_identity_user_id,action,target_type,
              target_id,result,request_id,metadata
            ) values(%s,%s,'platform.demo_inspection.revoked','demo_inspection',%s,
              'succeeded',%s,jsonb_build_object('trigger','demo_reset'))
            """,
            (seller_id, actor_identity_user_id, inspection_id, uuid4()),
        )

    for identity, identity_uuid in ((admin, admin_id), (buyer, buyer_id)):
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,%s,%s,%s,true)
            on conflict(identity_issuer,identity_sub) do update set
              email_normalized=excluded.email_normalized,email_verified=true,
              updated_at=clock_timestamp()
            where identity_user.id=excluded.id
            """,
            (identity_uuid, issuer, identity["subject"], identity["email"]),
        )
        row = connection.execute(
            "select id,email_normalized,email_verified from identity_user "
            "where identity_issuer=%s and identity_sub=%s",
            (issuer, identity["subject"]),
        ).fetchone()
        if row != (identity_uuid, identity["email"], True):
            raise RuntimeError("demo identity conflicts with existing identity")

    connection.execute(
        "update platform_grant set status='revoked',revoked_at=clock_timestamp() "
        "where identity_user_id=%s "
        "and role='platform_superadmin' and status='active'",
        (superadmin_id,),
    )
    if provision_superadmin:
        membership = connection.execute(
            "select exists(select 1 from seller_membership where identity_user_id=%s)",
            (superadmin_id,),
        ).fetchone()
        if membership != (False,):
            raise RuntimeError("demo superadmin identity must not have seller memberships")
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,%s,%s,%s,true)
            on conflict(identity_issuer,identity_sub) do update set
              email_normalized=excluded.email_normalized,email_verified=true,
              updated_at=clock_timestamp()
            where identity_user.id=excluded.id
            """,
            (
                superadmin_id,
                issuer,
                DEMO_SUPERADMIN_SUBJECT,
                DEMO_SUPERADMIN_EMAIL,
            ),
        )
        row = connection.execute(
            "select id,email_normalized,email_verified from identity_user "
            "where identity_issuer=%s and identity_sub=%s",
            (issuer, DEMO_SUPERADMIN_SUBJECT),
        ).fetchone()
        if row != (superadmin_id, DEMO_SUPERADMIN_EMAIL, True):
            raise RuntimeError("demo superadmin identity conflicts with existing identity")
        connection.execute(
            """
            insert into platform_grant(
              identity_user_id,role,status,granted_by_identity_user_id
            ) values(%s,'platform_superadmin','active',%s)
            on conflict(identity_user_id,role) do update set
              status='active',granted_by_identity_user_id=excluded.granted_by_identity_user_id,
              granted_at=clock_timestamp(),revoked_at=null
            """,
            (superadmin_id, superadmin_id),
        )

    connection.execute(
        """
        insert into identity_user(
          id,identity_issuer,identity_sub,email_normalized,email_verified
        ) values(%s,%s,%s,%s,true)
        on conflict(identity_issuer,identity_sub) do update set
          email_normalized=excluded.email_normalized,email_verified=true,
          updated_at=clock_timestamp()
        where identity_user.id=excluded.id
        """,
        (asset_worker_id, issuer, ASSET_WORKER_SUBJECT, ASSET_WORKER_EMAIL),
    )
    connection.execute(
        """
        insert into app_private.asset_worker_identity(identity_user_id,status,label)
        values(%s,'active','Local and CI private asset worker')
        on conflict(identity_user_id) do update set status='active',
          label=excluded.label,updated_at=clock_timestamp()
        """,
        (asset_worker_id,),
    )

    connection.execute(
        """
        insert into seller_organization(id,slug,display_name,status)
        values(%s,%s,%s,'active') on conflict(id) do update set
          slug=excluded.slug,display_name=excluded.display_name,status='active',
          updated_at=clock_timestamp()
        """,
        (seller_id, seller["slug"], seller["display_name"]),
    )
    connection.execute(
        "delete from platform_grant where identity_user_id in (%s,%s)",
        (admin_id, buyer_id),
    )
    connection.execute(
        "delete from seller_membership where identity_user_id=%s "
        "or (identity_user_id=%s and seller_organization_id<>%s)",
        (buyer_id, admin_id, seller_id),
    )
    connection.execute(
        "delete from seller_membership where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        """
        insert into seller_membership(seller_organization_id,identity_user_id,role,status)
        values(%s,%s,'seller_owner','active')
        on conflict(seller_organization_id,identity_user_id) do update set
          role='seller_owner',status='active',updated_at=clock_timestamp()
        """,
        (seller_id, admin_id),
    )

    # The demo tenant is disposable. Clear its complete mutable graph so a
    # previous presentation cannot leak extra products, courses or buyers.
    connection.execute(
        "delete from app_private.demo_tenant_registry where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.curriculum_mutation_request where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.catalog_mutation_request where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.product_draft_request where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.learning_progress_request where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.asset_mutation_request where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.asset_verification_job where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.asset_storage_cleanup_job where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.asset_upload where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute("delete from lesson_progress where seller_organization_id=%s", (seller_id,))
    connection.execute(
        "delete from course_learning_state where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        "delete from learning_enrollment where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        "delete from publication_asset where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        "delete from lesson_asset_draft where seller_organization_id=%s", (seller_id,)
    )
    connection.execute("delete from asset_version where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from asset_blob where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from asset_record where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from learning_asset where seller_organization_id=%s", (seller_id,))
    connection.execute(
        "delete from buyer_entitlement where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        "delete from publication_lesson where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        "delete from publication_course where seller_organization_id=%s", (seller_id,)
    )
    connection.execute("delete from lesson where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from course_module where seller_organization_id=%s", (seller_id,))
    connection.execute(
        "update catalog_product set status='draft',published_snapshot_id=null "
        "where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute("delete from publication where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from offer where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from product_course where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from course where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from catalog_product where seller_organization_id=%s", (seller_id,))
    connection.execute(
        """
        insert into catalog_product(
          seller_organization_id,id,slug,title,description,status,version
        ) values(%s,%s,%s,%s,%s,'draft',1)
        on conflict(seller_organization_id,id) do update set
          slug=excluded.slug,title=excluded.title,description=excluded.description,
          status=case when catalog_product.published_snapshot_id is null then 'draft'
            else catalog_product.status end,updated_at=clock_timestamp()
        """,
        (seller_id, product_id, product["slug"], product["title"], product["description"]),
    )
    connection.execute(
        """
        insert into course(
          seller_organization_id,id,title,description,status,draft_version
        ) values(%s,%s,%s,%s,'draft',1)
        on conflict(seller_organization_id,id) do update set
          title=excluded.title,description=excluded.description,status='draft',
          updated_at=clock_timestamp()
        """,
        (seller_id, course_id, course["title"], course["description"]),
    )
    connection.execute(
        """
        insert into product_course(
          seller_organization_id,catalog_product_id,course_id,position
        ) values(%s,%s,%s,0)
        on conflict(seller_organization_id,catalog_product_id,course_id)
        do update set position=0
        """,
        (seller_id, product_id, course_id),
    )
    connection.execute(
        """
        insert into offer(
          seller_organization_id,id,catalog_product_id,title,summary,status,version
        ) values(%s,%s,%s,%s,%s,'draft',1)
        on conflict(seller_organization_id,id) do update set
          title=excluded.title,summary=excluded.summary,status='draft',
          updated_at=clock_timestamp()
        """,
        (seller_id, offer_id, product_id, offer["title"], offer["summary"]),
    )

    connection.execute(
        """
        insert into course_module(
          seller_organization_id,id,course_id,title,status,position
        ) values(%s,%s,%s,%s,'active',0)
        """,
        (seller_id, module_id, course_id, module["title"]),
    )
    for lesson, lesson_id in zip(lessons, lesson_ids, strict=True):
        connection.execute(
            """
            insert into lesson(
              seller_organization_id,id,course_id,module_id,title,body_text,status,position
            ) values(%s,%s,%s,%s,%s,%s,'active',%s)
            """,
            (
                seller_id,
                lesson_id,
                course_id,
                module_id,
                lesson["title"],
                lesson["summary"],
                lesson["position"],
            ),
        )
        connection.execute(
            """
            insert into asset_record(
              seller_organization_id,id,catalog_product_id,title,status
            ) values(%s,%s,%s,%s,'active')
            """,
            (seller_id, UUID(lesson["asset_id"]), product_id, lesson["title"]),
        )
        connection.execute(
            """
            insert into asset_blob(
              seller_organization_id,id,storage_driver,storage_bucket,object_key,
              file_name,media_type,byte_size,sha256,magic_type,scanner_result,verified_at
            ) values(%s,app_private.legacy_asset_blob_id(%s),'legacy_bundle','codex-demo',
              %s,%s,'application/pdf',%s,%s,'application/pdf','legacy_trusted',
              clock_timestamp())
            """,
            (
                seller_id,
                UUID(lesson["asset_id"]),
                f"{lesson['slug']}.pdf",
                f"{lesson['slug']}.pdf",
                lesson["bytes"],
                lesson["sha256"],
            ),
        )
        connection.execute(
            """
            insert into asset_version(
              seller_organization_id,asset_id,version,blob_id,actor_identity_user_id
            ) values(%s,%s,1,app_private.legacy_asset_blob_id(%s),%s)
            """,
            (seller_id, UUID(lesson["asset_id"]), UUID(lesson["asset_id"]), admin_id),
        )
        connection.execute(
            """
            insert into lesson_asset_draft(
              seller_organization_id,catalog_product_id,course_id,module_id,lesson_id,
              asset_id,asset_version,blob_id,position,status
            ) values(%s,%s,%s,%s,%s,%s,1,app_private.legacy_asset_blob_id(%s),%s,'active')
            """,
            (
                seller_id,
                product_id,
                course_id,
                module_id,
                lesson_id,
                UUID(lesson["asset_id"]),
                UUID(lesson["asset_id"]),
                lesson["position"],
            ),
        )
        connection.execute(
            """
            insert into learning_asset(
              seller_organization_id,id,catalog_product_id,course_id,module_id,
              lesson_id,seed_key,file_name,title,media_type,byte_size,sha256,
              position,status
            ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,'application/pdf',%s,%s,%s,'active')
            """,
            (
                seller_id,
                UUID(lesson["asset_id"]),
                product_id,
                course_id,
                module_id,
                lesson_id,
                lesson["slug"],
                f"{lesson['slug']}.pdf",
                lesson["title"],
                lesson["bytes"],
                lesson["sha256"],
                lesson["position"],
            ),
        )
        video = manifest.get("video")
        if isinstance(video, dict) and video.get("lesson_id") == str(lesson_id):
            video_asset_id = UUID(video["asset_id"])
            connection.execute(
                """
                insert into asset_record(
                  seller_organization_id,id,catalog_product_id,title,status
                ) values(%s,%s,%s,%s,'active')
                """,
                (seller_id, video_asset_id, product_id, video["title"]),
            )
            connection.execute(
                """
                insert into asset_blob(
                  seller_organization_id,id,storage_driver,storage_bucket,object_key,
                  file_name,media_type,byte_size,sha256,magic_type,scanner_result,verified_at
                ) values(%s,app_private.legacy_asset_blob_id(%s),'legacy_bundle','codex-demo',
                  %s,%s,'video/mp4',%s,%s,'video/mp4','legacy_trusted',clock_timestamp())
                """,
                (
                    seller_id,
                    video_asset_id,
                    video["file_name"],
                    video["file_name"],
                    video["bytes"],
                    video["sha256"],
                ),
            )
            connection.execute(
                """
                insert into asset_version(
                  seller_organization_id,asset_id,version,blob_id,actor_identity_user_id
                ) values(%s,%s,1,app_private.legacy_asset_blob_id(%s),%s)
                """,
                (seller_id, video_asset_id, video_asset_id, admin_id),
            )
            connection.execute(
                """
                insert into lesson_asset_draft(
                  seller_organization_id,catalog_product_id,course_id,module_id,lesson_id,
                  asset_id,asset_version,blob_id,position,status
                ) values(%s,%s,%s,%s,%s,%s,1,app_private.legacy_asset_blob_id(%s),%s,'active')
                """,
                (
                    seller_id,
                    product_id,
                    course_id,
                    module_id,
                    lesson_id,
                    video_asset_id,
                    video_asset_id,
                    video["position"],
                ),
            )
            connection.execute(
                """
                insert into learning_asset(
                  seller_organization_id,id,catalog_product_id,course_id,module_id,
                  lesson_id,seed_key,file_name,title,media_type,byte_size,sha256,
                  position,status
                ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,'video/mp4',%s,%s,%s,'active')
                """,
                (
                    seller_id,
                    video_asset_id,
                    product_id,
                    course_id,
                    module_id,
                    lesson_id,
                    video["slug"],
                    video["file_name"],
                    video["title"],
                    video["bytes"],
                    video["sha256"],
                    video["position"],
                ),
            )

    snapshot = snapshot_for(manifest)
    connection.execute(
        """
        with payload as (select %s::jsonb as snapshot)
        insert into publication(
          seller_organization_id,id,catalog_product_id,version,schema_version,
          source_sha256,snapshot,actor_identity_user_id
        ) select %s,%s,%s,1,3,
          encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),
          snapshot,%s from payload
        on conflict(seller_organization_id,id) do nothing
        """,
        (Jsonb(snapshot), seller_id, publication_id, product_id, admin_id),
    )
    baseline = connection.execute(
        "select snapshot from publication where seller_organization_id=%s and id=%s",
        (seller_id, publication_id),
    ).fetchone()
    if baseline != (snapshot,):
        raise RuntimeError("immutable baseline publication differs from manifest")
    connection.execute(
        """
        update catalog_product set status='published',published_snapshot_id=%s,
          slug=%s,title=%s,description=%s,updated_at=clock_timestamp()
        where seller_organization_id=%s and id=%s
        """,
        (
            publication_id,
            product["slug"],
            product["title"],
            product["description"],
            seller_id,
            product_id,
        ),
    )
    connection.execute(
        "update course set status='published',updated_at=clock_timestamp() "
        "where seller_organization_id=%s and id=%s",
        (seller_id, course_id),
    )
    connection.execute(
        """
        insert into buyer_entitlement(
          seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
          publication_id,reference_key,source,status,progress_percent
        ) values(%s,%s,%s,%s,%s,'demo-user-codex-v1','demo_seed','active',0)
        on conflict(seller_organization_id,buyer_identity_user_id,catalog_product_id)
        do update set publication_id=excluded.publication_id,status='active',
          progress_percent=0,starts_at=clock_timestamp(),expires_at=null,
          updated_at=clock_timestamp()
        """,
        (seller_id, entitlement_id, buyer_id, product_id, publication_id),
    )
    connection.execute(
        """
        insert into app_private.demo_tenant_registry(
          seed_key,seed_version,manifest_sha256,identity_issuer,
          admin_identity_user_id,buyer_identity_user_id,seller_organization_id,
          catalog_product_id,course_id,offer_id,baseline_publication_id
        ) values(%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(seed_key) do update set
          seed_version=excluded.seed_version,manifest_sha256=excluded.manifest_sha256,
          identity_issuer=excluded.identity_issuer,
          admin_identity_user_id=excluded.admin_identity_user_id,
          buyer_identity_user_id=excluded.buyer_identity_user_id,
          seller_organization_id=excluded.seller_organization_id,
          catalog_product_id=excluded.catalog_product_id,course_id=excluded.course_id,
          offer_id=excluded.offer_id,
          baseline_publication_id=excluded.baseline_publication_id,
          reset_at=clock_timestamp()
        """,
        (
            DEMO_KEY,
            manifest_sha,
            issuer,
            admin_id,
            buyer_id,
            seller_id,
            product_id,
            course_id,
            offer_id,
            publication_id,
        ),
    )
    connection.execute(
        """
        insert into audit_event(
          seller_organization_id,actor_identity_user_id,action,target_type,
          target_id,result,request_id,metadata
        ) values(%s,%s,'demo.tenant_reset','seller_organization',%s,
          'succeeded',%s,jsonb_build_object(
            'seed_key',%s::text,'manifest_sha256',%s::text,
            'buyer_identity_user_id',%s::uuid
          ))
        """,
        (seller_id, admin_id, seller_id, uuid4(), DEMO_KEY, manifest_sha, buyer_id),
    )


def main() -> None:
    args = parse_args()
    issuer, database_url, provision_superadmin = require_environment(args)
    manifest, manifest_sha = load_manifest()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "valid",
                    "seed_key": DEMO_KEY,
                    "manifest_sha256": manifest_sha,
                    "seller_id": manifest["seller"]["id"],
                    "admin_email": manifest["personas"]["admin"]["email"],
                    "buyer_email": manifest["personas"]["buyer"]["email"],
                    "superadmin_email": DEMO_SUPERADMIN_EMAIL if provision_superadmin else None,
                    "asset_worker_identity_user_id": str(identity_id(issuer, ASSET_WORKER_SUBJECT)),
                    "lessons": len(manifest["lessons"]),
                }
            )
        )
        return
    if args.prepare_storage_cleanup:
        with psycopg.connect(database_url) as connection:
            pending_cleanup_jobs = prepare_demo_storage_cleanup(connection, manifest)
            connection.commit()
        print(
            json.dumps(
                {
                    "status": "storage_cleanup_prepared",
                    "seed_key": DEMO_KEY,
                    "pending_cleanup_jobs": pending_cleanup_jobs,
                }
            )
        )
        return
    with psycopg.connect(database_url) as connection:
        reset_demo(
            connection,
            manifest,
            manifest_sha,
            issuer,
            provision_superadmin=provision_superadmin,
        )
        connection.commit()
    print(
        json.dumps(
            {
                "status": "reset",
                "seed_key": DEMO_KEY,
                "manifest_sha256": manifest_sha,
                "superadmin_email": DEMO_SUPERADMIN_EMAIL if provision_superadmin else None,
                "asset_worker_identity_user_id": str(identity_id(issuer, ASSET_WORKER_SUBJECT)),
            }
        )
    )


if __name__ == "__main__":
    main()
