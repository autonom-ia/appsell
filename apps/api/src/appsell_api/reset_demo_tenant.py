from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

DEMO_KEY = "autonomia-demo-v1"
DEMO_DATABASE_MARKER = "appsell-demo-reset-target-v1"
ASSET_WORKER_SUBJECT = "asset-worker-v1"
ASSET_WORKER_EMAIL = "asset.worker@autonomia.site"
CHECKOUT_WORKER_SUBJECT = "demo-checkout-worker-v1"
CHECKOUT_WORKER_EMAIL = "checkout.worker@autonomia.site"
DEMO_SUPERADMIN_SUBJECT = "demo-superadmin"
DEMO_SUPERADMIN_EMAIL = "comercial@autonomia.site"
EXPECTED_MIGRATION = "0038_player_hides_materials"


class RemoteResetTarget(TypedDict):
    user: str
    host: str
    port: str
    dbname: str


class RemoteResetProfile(TypedDict):
    issuer: str
    target: RemoteResetTarget


REMOTE_RESET_PROFILES: dict[str, RemoteResetProfile] = {
    "appsell-runtime-preview-77": {
        "issuer": "https://identity.runtime-preview.local",
        "target": {
            "user": "appsell_preview_77_migrator",
            "host": "sell-autonomia_postgres",
            "port": "5432",
            "dbname": "appsell_preview_77",
        },
    },
    "appsell-native-90": {
        "issuer": "https://identity.native.local",
        "target": {
            "user": "appsell_native_90_migrator",
            "host": "sell-autonomia_postgres-native-90",
            "port": "5432",
            "dbname": "appsell_native_90",
        },
    },
}
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
    if manifest.get("seed_key") != DEMO_KEY or manifest.get("version") != 2:
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
    checkout = manifest.get("checkout")
    if (
        not isinstance(checkout, dict)
        or checkout.get("coupon_code") != "AUTONOMIA100"
        or checkout.get("unit_amount_minor") != 9900
        or checkout.get("currency") != "BRL"
        or not isinstance(checkout.get("coupon_id"), str)
    ):
        raise RuntimeError("invalid demo checkout manifest")
    UUID(checkout["coupon_id"])
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
        raster = lesson.get("raster")
        pages = raster.get("pages") if isinstance(raster, dict) else None
        if (
            not isinstance(raster, dict)
            or raster.get("profile") != "png-1600-v1"
            or not isinstance(pages, list)
            or not 1 <= len(pages) <= 100
            or raster.get("page_count") != len(pages)
            or not isinstance(raster.get("manifest_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", raster["manifest_sha256"])
        ):
            raise RuntimeError(f"invalid demo PDF raster manifest: {file_name}")
        total_page_bytes = 0
        for page_number, page in enumerate(pages, start=1):
            expected_key = f"{lesson['slug']}/pages/{page_number}.png"
            if (
                not isinstance(page, dict)
                or page.get("page_number") != page_number
                or page.get("storage_driver") != "legacy_bundle"
                or page.get("storage_bucket") != "codex-demo"
                or page.get("object_key") != expected_key
                or page.get("storage_version_id") is not None
                or page.get("storage_etag") is not None
                or not isinstance(page.get("byte_size"), int)
                or not 1 <= page["byte_size"] <= 8 * 1024 * 1024
                or not isinstance(page.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", page["sha256"])
                or not isinstance(page.get("pixel_width"), int)
                or not isinstance(page.get("pixel_height"), int)
                or not 1 <= page["pixel_width"] <= 1_600
                or not 1 <= page["pixel_height"] <= 1_600
                or page["pixel_width"] * page["pixel_height"] > 1_600**2
            ):
                raise RuntimeError(f"invalid demo PDF raster page: {expected_key}")
            page_payload = (PDF_ROOT / expected_key).read_bytes()
            if len(page_payload) != page["byte_size"]:
                raise RuntimeError(f"demo PDF raster size mismatch: {expected_key}")
            if hashlib.sha256(page_payload).hexdigest() != page["sha256"]:
                raise RuntimeError(f"demo PDF raster hash mismatch: {expected_key}")
            total_page_bytes += page["byte_size"]
        if total_page_bytes != raster.get("total_bytes") or total_page_bytes > 128 * 1024 * 1024:
            raise RuntimeError(f"demo PDF raster total mismatch: {file_name}")
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
    remote_scope = os.environ.get("APPSELL_DEMO_REMOTE_RESET_SCOPE", "").strip()
    remote_profile = REMOTE_RESET_PROFILES.get(remote_scope)
    is_loopback = connection_info.get("host") in {"127.0.0.1", "localhost", "::1"}
    if is_loopback and not connection_info.get("hostaddr"):
        if remote_scope:
            raise RuntimeError("remote demo reset scope is forbidden for a loopback database")
    else:
        allowed_connection_fields = {"user", "password", "host", "port", "dbname"}
        if (
            remote_profile is None
            or node_environment != "test"
            or app_environment != "ci"
            or environment != "ci"
            or issuer != remote_profile["issuer"]
            or any(
                connection_info.get(key) != value for key, value in remote_profile["target"].items()
            )
            or set(connection_info) != allowed_connection_fields
            or not connection_info.get("password")
        ):
            raise RuntimeError(
                "demo reset database must be loopback-only or an exact approved remote target"
            )
    superadmin_value = os.environ.get("APPSELL_DEMO_SUPERADMIN_ENABLED", "").strip().lower()
    if superadmin_value not in {"", "false", "true"}:
        raise RuntimeError("APPSELL_DEMO_SUPERADMIN_ENABLED must explicitly be true or false")
    return issuer, database_url, superadmin_value == "true"


def validate_demo_database(connection: psycopg.Connection[Any]) -> None:
    marker = connection.execute(
        "select current_database(),shobj_description(oid,'pg_database') from pg_database "
        "where datname=current_database()"
    ).fetchone()
    remote_scope = os.environ.get("APPSELL_DEMO_REMOTE_RESET_SCOPE", "").strip()
    remote_profile = REMOTE_RESET_PROFILES.get(remote_scope)
    expected_database = (
        remote_profile["target"]["dbname"]
        if remote_profile is not None
        else marker[0]
        if marker
        else None
    )
    if marker != (expected_database, DEMO_DATABASE_MARKER):
        raise RuntimeError("database is not marked as an AppSell demo reset target")


def snapshot_assets(manifest: dict[str, Any], lesson: dict[str, Any]) -> list[dict[str, Any]]:
    downloadable_support = lesson["lesson_id"] == manifest["lessons"][-1]["lesson_id"]
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
            "content_role": "support_material" if downloadable_support else "course_content",
            "download_allowed": downloadable_support,
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
                "content_role": "course_content",
                "download_allowed": False,
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
        "schema_version": 4,
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
    checkout = manifest["checkout"]
    publication = manifest["publication"]
    entitlement = manifest["entitlement"]
    lessons = manifest["lessons"]
    admin = manifest["personas"]["admin"]
    buyer = manifest["personas"]["buyer"]
    admin_id = identity_id(issuer, admin["subject"])
    buyer_id = identity_id(issuer, buyer["subject"])
    superadmin_id = identity_id(issuer, DEMO_SUPERADMIN_SUBJECT)
    asset_worker_id = identity_id(issuer, ASSET_WORKER_SUBJECT)
    checkout_worker_id = identity_id(issuer, CHECKOUT_WORKER_SUBJECT)
    seller_id = UUID(seller["id"])
    product_id = UUID(product["id"])
    course_id = UUID(course["id"])
    module_id = UUID(module["id"])
    offer_id = UUID(offer["id"])
    publication_id = UUID(publication["id"])
    entitlement_id = UUID(entitlement["id"])
    coupon_id = UUID(checkout["coupon_id"])
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
        insert into identity_user(
          id,identity_issuer,identity_sub,email_normalized,email_verified
        ) values(%s,%s,%s,%s,true)
        on conflict(identity_issuer,identity_sub) do update set
          email_normalized=excluded.email_normalized,email_verified=true,
          updated_at=clock_timestamp()
        where identity_user.id=excluded.id
        """,
        (checkout_worker_id, issuer, CHECKOUT_WORKER_SUBJECT, CHECKOUT_WORKER_EMAIL),
    )
    connection.execute(
        """
        insert into app_private.demo_checkout_worker_identity(identity_user_id,status)
        values(%s,'active') on conflict(identity_user_id) do update set status='active'
        """,
        (checkout_worker_id,),
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
        "delete from app_private.demo_checkout_request where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.demo_checkout_outbox where seller_organization_id=%s",
        (seller_id,),
    )
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
        "delete from app_private.asset_pdf_page_candidate where seller_organization_id=%s",
        (seller_id,),
    )
    connection.execute(
        "delete from app_private.asset_pdf_raster_candidate where seller_organization_id=%s",
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
        "delete from buyer_entitlement where seller_organization_id=%s", (seller_id,)
    )
    connection.execute("delete from demo_purchase where seller_organization_id=%s", (seller_id,))
    connection.execute(
        "delete from demo_purchase_event where seller_organization_id=%s", (seller_id,)
    )
    connection.execute("delete from demo_order where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from demo_coupon where seller_organization_id=%s", (seller_id,))
    connection.execute(
        "delete from demo_checkout_offer where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        "delete from publication_asset where seller_organization_id=%s", (seller_id,)
    )
    connection.execute(
        "delete from lesson_asset_draft where seller_organization_id=%s", (seller_id,)
    )
    connection.execute("delete from asset_pdf_page where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from asset_pdf_raster where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from asset_version where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from asset_blob where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from asset_record where seller_organization_id=%s", (seller_id,))
    connection.execute("delete from learning_asset where seller_organization_id=%s", (seller_id,))
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
        raster = lesson["raster"]
        connection.execute(
            """
            insert into asset_pdf_raster(
              seller_organization_id,asset_id,asset_version,blob_id,raster_profile,
              page_count,total_byte_size,manifest_sha256
            ) values(%s,%s,1,app_private.legacy_asset_blob_id(%s),%s,%s,%s,%s)
            """,
            (
                seller_id,
                UUID(lesson["asset_id"]),
                UUID(lesson["asset_id"]),
                raster["profile"],
                raster["page_count"],
                raster["total_bytes"],
                raster["manifest_sha256"],
            ),
        )
        for page in raster["pages"]:
            connection.execute(
                """
                insert into asset_pdf_page(
                  seller_organization_id,asset_id,asset_version,page_number,storage_driver,
                  storage_bucket,object_key,storage_version_id,storage_etag,byte_size,sha256,
                  pixel_width,pixel_height
                ) values(%s,%s,1,%s,'legacy_bundle','codex-demo',%s,null,null,%s,%s,%s,%s)
                """,
                (
                    seller_id,
                    UUID(lesson["asset_id"]),
                    page["page_number"],
                    page["object_key"],
                    page["byte_size"],
                    page["sha256"],
                    page["pixel_width"],
                    page["pixel_height"],
                ),
            )
        connection.execute(
            """
            insert into lesson_asset_draft(
              seller_organization_id,catalog_product_id,course_id,module_id,lesson_id,
              asset_id,asset_version,blob_id,position,status,content_role,download_allowed
            ) values(%s,%s,%s,%s,%s,%s,1,app_private.legacy_asset_blob_id(%s),%s,
              'active',%s,%s)
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
                "support_material"
                if lesson["lesson_id"] == manifest["lessons"][-1]["lesson_id"]
                else "course_content",
                lesson["lesson_id"] == manifest["lessons"][-1]["lesson_id"],
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
        ) select %s,%s,%s,1,4,
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
        insert into app_private.demo_tenant_registry(
          seed_key,seed_version,manifest_sha256,identity_issuer,
          admin_identity_user_id,buyer_identity_user_id,seller_organization_id,
          catalog_product_id,course_id,offer_id,baseline_publication_id
        ) values(%s,2,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
        insert into demo_checkout_offer(
          seller_organization_id,offer_id,catalog_product_id,publication_id,
          entitlement_id,unit_amount_minor,currency,status
        ) values(%s,%s,%s,%s,%s,%s,%s,'active')
        """,
        (
            seller_id,
            offer_id,
            product_id,
            publication_id,
            entitlement_id,
            checkout["unit_amount_minor"],
            checkout["currency"],
        ),
    )
    connection.execute(
        """
        insert into demo_coupon(
          seller_organization_id,id,code_sha256,discount_bps,status
        ) values(%s,%s,%s,10000,'active')
        """,
        (
            seller_id,
            coupon_id,
            hashlib.sha256(checkout["coupon_code"].encode("utf-8")).hexdigest(),
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
