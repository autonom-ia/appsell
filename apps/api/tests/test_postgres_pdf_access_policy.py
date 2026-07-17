from __future__ import annotations

import hashlib
import hmac
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
MIGRATOR_URL = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

OWNER = UUID("81000000-0000-4000-8000-000000000001")
ADMIN = UUID("81000000-0000-4000-8000-000000000002")
EDITOR = UUID("81000000-0000-4000-8000-000000000003")
BUYER = UUID("81000000-0000-4000-8000-000000000004")
OTHER_OWNER = UUID("81000000-0000-4000-8000-000000000005")


@dataclass(frozen=True)
class Graph:
    seller: UUID
    product: UUID
    course: UUID
    module: UUID
    lesson: UUID
    offer: UUID
    owner: UUID


@dataclass(frozen=True)
class Asset:
    asset: UUID
    blob: UUID
    media_type: str
    file_name: str
    position: int


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sign_context(
    actor: UUID, seller: UUID | None, capability: str, issued: int, expires: int
) -> str:
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    return hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


def apply_context(
    connection: psycopg.Connection[Any],
    actor: UUID,
    seller: UUID | None,
    capability: str,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (
            actor,
            seller,
            capability,
            issued,
            expires,
            sign_context(actor, seller, capability, issued, expires),
        ),
    )


def seed_identities(connection: psycopg.Connection[Any]) -> None:
    identities = (
        (OWNER, "policy-owner", "policy-owner@example.test"),
        (ADMIN, "policy-admin", "policy-admin@example.test"),
        (EDITOR, "policy-editor", "policy-editor@example.test"),
        (BUYER, "policy-buyer", "policy-buyer@example.test"),
        (OTHER_OWNER, "policy-other-owner", "policy-other-owner@example.test"),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,'https://identity.pdf-policy.test',%s,%s,true)
            on conflict(id) do nothing
            """,
            identities,
        )


@pytest.fixture(scope="module", autouse=True)
def identities() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seed_identities(connection)
        connection.commit()


def create_graph(
    connection: psycopg.Connection[Any], *, owner: UUID = OWNER, staff: bool = True
) -> Graph:
    suffix = uuid4().hex[:12]
    graph = Graph(
        seller=uuid4(),
        product=uuid4(),
        course=uuid4(),
        module=uuid4(),
        lesson=uuid4(),
        offer=uuid4(),
        owner=owner,
    )
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) values(%s,%s,%s,'active')",
        (graph.seller, f"pdf-policy-{suffix}", f"PDF Policy {suffix}"),
    )
    connection.execute(
        "insert into seller_membership("
        "seller_organization_id,identity_user_id,role,status) "
        "values(%s,%s,'seller_owner','active')",
        (graph.seller, graph.owner),
    )
    if staff:
        connection.execute(
            """
            insert into seller_membership(
              seller_organization_id,identity_user_id,role,status
            ) values(%s,%s,'seller_admin','active'),(%s,%s,'seller_editor','active')
            """,
            (graph.seller, ADMIN, graph.seller, EDITOR),
        )
    connection.execute(
        "insert into catalog_product("
        "seller_organization_id,id,slug,title,description) "
        "values(%s,%s,%s,'PDF Policy Product','Product for policy tests')",
        (graph.seller, graph.product, f"pdf-policy-product-{suffix}"),
    )
    connection.execute(
        "insert into course(seller_organization_id,id,title,description) "
        "values(%s,%s,'PDF Policy Course','Course for policy tests')",
        (graph.seller, graph.course),
    )
    connection.execute(
        "insert into product_course("
        "seller_organization_id,catalog_product_id,course_id,position) "
        "values(%s,%s,%s,0)",
        (graph.seller, graph.product, graph.course),
    )
    connection.execute(
        "insert into course_module("
        "seller_organization_id,id,course_id,title,position) "
        "values(%s,%s,%s,'PDF Policy Module',0)",
        (graph.seller, graph.module, graph.course),
    )
    connection.execute(
        "insert into lesson("
        "seller_organization_id,id,course_id,module_id,title,body_text,position) "
        "values(%s,%s,%s,%s,'PDF Policy Lesson','Protected lesson',0)",
        (graph.seller, graph.lesson, graph.course, graph.module),
    )
    connection.execute(
        "insert into offer(seller_organization_id,id,catalog_product_id,title,summary) "
        "values(%s,%s,%s,'PDF Policy Offer','External checkout')",
        (graph.seller, graph.offer, graph.product),
    )
    return graph


def add_asset(
    connection: psycopg.Connection[Any],
    graph: Graph,
    *,
    media_type: str,
    position: int,
) -> Asset:
    asset_id, blob_id = uuid4(), uuid4()
    extension = "pdf" if media_type == "application/pdf" else "mp4"
    file_name = f"policy-{asset_id.hex}.{extension}"
    object_key = f"pdf-policy/{blob_id}.{extension}"
    title = f"Policy {extension.upper()} {position}"
    asset = Asset(asset_id, blob_id, media_type, file_name, position)
    connection.execute(
        "insert into asset_record(seller_organization_id,id,catalog_product_id,title) "
        "values(%s,%s,%s,%s)",
        (graph.seller, asset.asset, graph.product, title),
    )
    connection.execute(
        """
        insert into asset_blob(
          seller_organization_id,id,storage_driver,storage_bucket,object_key,
          file_name,media_type,byte_size,sha256,magic_type,scanner_result,verified_at
        ) values(%s,%s,'legacy_bundle','pytest-policy',%s,%s,%s,1024,%s,%s,
          'legacy_trusted',clock_timestamp())
        """,
        (
            graph.seller,
            asset.blob,
            object_key,
            asset.file_name,
            asset.media_type,
            digest(object_key),
            asset.media_type,
        ),
    )
    connection.execute(
        "insert into asset_version("
        "seller_organization_id,asset_id,version,blob_id,actor_identity_user_id) "
        "values(%s,%s,1,%s,%s)",
        (graph.seller, asset.asset, asset.blob, graph.owner),
    )
    connection.execute(
        """
        insert into lesson_asset_draft(
          seller_organization_id,catalog_product_id,course_id,module_id,lesson_id,
          asset_id,asset_version,blob_id,position,status
        ) values(%s,%s,%s,%s,%s,%s,1,%s,%s,'active')
        """,
        (
            graph.seller,
            graph.product,
            graph.course,
            graph.module,
            graph.lesson,
            asset.asset,
            asset.blob,
            position,
        ),
    )
    return asset


def set_policy(
    actor: UUID,
    graph: Graph,
    asset: Asset,
    *,
    content_role: str,
    download_allowed: bool,
    expected_version: int,
    key: UUID | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    assert RUNTIME_URL
    idempotency_key = key or uuid4()
    body_sha256 = body or digest(
        f"{asset.asset}:{content_role}:{download_allowed}:{expected_version}"
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, graph.seller, "catalog.asset.policy.update")
        row = connection.execute(
            """
            select app_private.set_lesson_asset_policy(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                actor,
                graph.seller,
                graph.product,
                graph.course,
                graph.module,
                graph.lesson,
                asset.asset,
                content_role,
                download_allowed,
                expected_version,
                idempotency_key,
                body_sha256,
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
        return cast(dict[str, Any], row[0])


def publish(graph: Graph, *, expected_course_version: int) -> UUID:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, graph.owner, graph.seller, "catalog.publication.create")
        row = connection.execute(
            """
            select publication_id from app_private.publish_product(
              %s,%s,%s,%s,1,1,%s,%s,%s,%s
            )
            """,
            (
                graph.owner,
                graph.seller,
                graph.product,
                graph.offer,
                Jsonb({str(graph.course): expected_course_version}),
                uuid4(),
                digest(f"publish:{graph.product}:{expected_course_version}"),
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
        return cast(UUID, row[0])


def seed_entitlement(graph: Graph, publication: UUID) -> UUID:
    assert ADMIN_URL
    entitlement = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into buyer_entitlement(
              seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
              publication_id,reference_key,source,status,progress_percent
            ) values(%s,%s,%s,%s,%s,%s,'manual','active',0)
            """,
            (
                graph.seller,
                entitlement,
                BUYER,
                graph.product,
                publication,
                f"pytest-policy-{entitlement.hex}",
            ),
        )
        connection.commit()
    return entitlement


def seed_legacy_publication(
    connection: psycopg.Connection[Any], graph: Graph, asset: Asset
) -> UUID:
    publication = uuid4()
    snapshot = {
        "schema_version": 2,
        "seller_organization_id": str(graph.seller),
        "product": {"id": str(graph.product), "title": "Legacy PDF Policy"},
        "offer": {"id": str(graph.offer)},
        "courses": [
            {
                "id": str(graph.course),
                "title": "Legacy Course",
                "position": 0,
                "modules": [
                    {
                        "id": str(graph.module),
                        "title": "Legacy Module",
                        "position": 0,
                        "lessons": [
                            {
                                "id": str(graph.lesson),
                                "title": "Legacy Lesson",
                                "content": "Legacy",
                                "position": 0,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    connection.execute(
        """
        insert into publication(
          seller_organization_id,id,catalog_product_id,version,schema_version,
          source_sha256,snapshot,actor_identity_user_id
        ) values(%s,%s,%s,1,2,%s,%s,%s)
        """,
        (
            graph.seller,
            publication,
            graph.product,
            digest(str(snapshot)),
            Jsonb(snapshot),
            graph.owner,
        ),
    )
    connection.execute(
        """
        insert into publication_asset(
          seller_organization_id,catalog_product_id,publication_id,course_id,module_id,
          lesson_id,asset_id,asset_version,blob_id,position,title,file_name,media_type,
          byte_size,sha256,storage_driver,storage_bucket,object_key
        ) select %s,%s,%s,%s,%s,%s,%s,1,%s,%s,record.title,blob.file_name,
          blob.media_type,blob.byte_size,blob.sha256,blob.storage_driver,blob.storage_bucket,
          blob.object_key
        from asset_record record join asset_blob blob
          on blob.seller_organization_id=record.seller_organization_id and blob.id=%s
        where record.seller_organization_id=%s and record.id=%s
        """,
        (
            graph.seller,
            graph.product,
            publication,
            graph.course,
            graph.module,
            graph.lesson,
            asset.asset,
            asset.blob,
            asset.position,
            asset.blob,
            graph.seller,
            asset.asset,
        ),
    )
    return publication


@pytest.mark.skipif(not MIGRATOR_URL, reason="migrator URL is required for upgrade test")
def test_0016_upgrade_backfills_existing_assets_fail_closed() -> None:
    assert ADMIN_URL and MIGRATOR_URL
    database_name = f"appsell_pdf_policy_upgrade_{uuid4().hex}"
    maintenance_url = make_conninfo(ADMIN_URL, dbname="postgres")
    target_url = make_conninfo(ADMIN_URL, dbname=database_name)
    migrator_role = make_url(MIGRATOR_URL).username
    assert migrator_role
    migrator_url = (
        make_url(MIGRATOR_URL).set(database=database_name).render_as_string(hide_password=False)
    )
    repository_root = Path(__file__).resolve().parents[3]
    with psycopg.connect(maintenance_url, autocommit=True) as connection:
        connection.execute(sql.SQL("create database {}").format(sql.Identifier(database_name)))
        connection.execute(
            sql.SQL("alter database {} owner to appsell_owner").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("revoke connect on database {} from public").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("grant connect on database {} to {},appsell_runtime").format(
                sql.Identifier(database_name), sql.Identifier(migrator_role)
            )
        )
    environment = {**os.environ, "APPSELL_MIGRATOR_DATABASE_URL": migrator_url}
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "upgrade",
                "0015_demo_zero_checkout",
            ],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        with psycopg.connect(target_url) as connection:
            seed_identities(connection)
            graph = create_graph(connection)
            asset = add_asset(connection, graph, media_type="application/pdf", position=0)
            publication = seed_legacy_publication(connection, graph, asset)
            connection.commit()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "upgrade",
                "0016_pdf_access_policy",
            ],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        with psycopg.connect(target_url) as connection:
            assert connection.execute(
                "select content_role,download_allowed from lesson_asset_draft "
                "where seller_organization_id=%s and asset_id=%s",
                (graph.seller, asset.asset),
            ).fetchone() == ("course_content", False)
            assert connection.execute(
                "select content_role,download_allowed from publication_asset "
                "where seller_organization_id=%s and publication_id=%s and asset_id=%s",
                (graph.seller, publication, asset.asset),
            ).fetchone() == ("course_content", False)
            assert connection.execute(
                "select (select version_num from alembic_version),"
                "to_regprocedure('app_private.read_learning_player_v3(uuid,uuid,uuid)') "
                "is not null,exists(select 1 from pg_trigger "
                "where tgrelid='public.lesson_asset_draft'::regclass "
                "and tgname='lesson_asset_draft_policy_valid' and tgenabled='O')"
            ).fetchone() == ("0016_pdf_access_policy", True, True)
    finally:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            connection.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname=%s and pid<>pg_backend_pid()",
                (database_name,),
            )
            connection.execute(
                sql.SQL("drop database if exists {}").format(sql.Identifier(database_name))
            )


def test_policy_defaults_and_pdf_video_constraints() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection)
        pdf = add_asset(connection, graph, media_type="application/pdf", position=0)
        video = add_asset(connection, graph, media_type="video/mp4", position=1)
        connection.commit()
        assert connection.execute(
            "select content_role,download_allowed from lesson_asset_draft "
            "where seller_organization_id=%s and asset_id=%s",
            (graph.seller, pdf.asset),
        ).fetchone() == ("course_content", False)
        connection.execute(
            "update lesson_asset_draft set content_role='support_material',"
            "download_allowed=true where seller_organization_id=%s and asset_id=%s",
            (graph.seller, pdf.asset),
        )
        connection.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "update lesson_asset_draft set content_role='support_material',"
                "download_allowed=true where seller_organization_id=%s and asset_id=%s",
                (graph.seller, video.asset),
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "update lesson_asset_draft set content_role='course_content',"
                "download_allowed=true where seller_organization_id=%s and asset_id=%s",
                (graph.seller, pdf.asset),
            )
        connection.rollback()
        definition = connection.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid='public.publication_asset'::regclass "
            "and conname='publication_asset_download_policy_check'"
        ).fetchone()
        assert definition is not None
        assert "application/pdf" in definition[0]
        publication = seed_legacy_publication(connection, graph, pdf)
        connection.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                insert into publication_asset(
                  seller_organization_id,catalog_product_id,publication_id,course_id,
                  module_id,lesson_id,asset_id,asset_version,blob_id,position,title,
                  file_name,media_type,byte_size,sha256,storage_driver,storage_bucket,
                  object_key,content_role,download_allowed
                ) select %s,%s,%s,%s,%s,%s,%s,1,%s,%s,record.title,blob.file_name,
                  blob.media_type,blob.byte_size,blob.sha256,blob.storage_driver,
                  blob.storage_bucket,blob.object_key,'support_material',true
                from asset_record record join asset_blob blob
                  on blob.seller_organization_id=record.seller_organization_id and blob.id=%s
                where record.seller_organization_id=%s and record.id=%s
                """,
                (
                    graph.seller,
                    graph.product,
                    publication,
                    graph.course,
                    graph.module,
                    graph.lesson,
                    video.asset,
                    video.blob,
                    video.position,
                    video.blob,
                    graph.seller,
                    video.asset,
                ),
            )
        connection.rollback()


def test_policy_mutation_roles_tenant_idempotency_and_version() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection)
        asset = add_asset(connection, graph, media_type="application/pdf", position=0)
        other = create_graph(connection, owner=OTHER_OWNER, staff=False)
        other_asset = add_asset(connection, other, media_type="application/pdf", position=0)
        connection.commit()

    key = uuid4()
    body = digest("owner-policy-idempotency")
    first = set_policy(
        OWNER,
        graph,
        asset,
        content_role="support_material",
        download_allowed=True,
        expected_version=1,
        key=key,
        body=body,
    )
    assert first["course_version"] == 2 and first["applied"] is True
    with psycopg.connect(ADMIN_URL) as connection:
        audit = connection.execute(
            "select action,target_type,target_id,metadata from audit_event "
            "where seller_organization_id=%s and target_id=%s "
            "order by created_at desc limit 1",
            (graph.seller, asset.asset),
        ).fetchone()
    assert audit is not None
    assert audit[0:3] == ("catalog.asset_policy_set", "asset", asset.asset)
    assert audit[3]["previous_content_role"] == "course_content"
    assert audit[3]["content_role"] == "support_material"
    assert audit[3]["previous_download_allowed"] is False
    assert audit[3]["download_allowed"] is True
    replay = set_policy(
        OWNER,
        graph,
        asset,
        content_role="support_material",
        download_allowed=True,
        expected_version=1,
        key=key,
        body=body,
    )
    assert replay["course_version"] == 2
    assert replay["applied"] is False and replay["replayed"] is True
    with pytest.raises(psycopg.errors.UniqueViolation):
        set_policy(
            OWNER,
            graph,
            asset,
            content_role="support_material",
            download_allowed=False,
            expected_version=2,
            key=key,
            body=body,
        )
    with pytest.raises(psycopg.errors.SerializationFailure):
        set_policy(
            OWNER,
            graph,
            asset,
            content_role="course_content",
            download_allowed=False,
            expected_version=1,
        )
    admin_result = set_policy(
        ADMIN,
        graph,
        asset,
        content_role="course_content",
        download_allowed=False,
        expected_version=2,
    )
    assert admin_result["course_version"] == 3
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        set_policy(
            EDITOR,
            graph,
            asset,
            content_role="support_material",
            download_allowed=False,
            expected_version=3,
        )
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        set_policy(
            OWNER,
            other,
            other_asset,
            content_role="support_material",
            download_allowed=True,
            expected_version=1,
        )


def test_publication_pins_policy_players_and_download_authorization() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection)
        pdf = add_asset(connection, graph, media_type="application/pdf", position=0)
        video = add_asset(connection, graph, media_type="video/mp4", position=1)
        connection.commit()
    set_policy(
        OWNER,
        graph,
        pdf,
        content_role="support_material",
        download_allowed=True,
        expected_version=1,
    )
    publication = publish(graph, expected_course_version=2)
    with psycopg.connect(ADMIN_URL) as connection:
        publication_row = connection.execute(
            "select schema_version,snapshot from publication "
            "where seller_organization_id=%s and id=%s",
            (graph.seller, publication),
        ).fetchone()
        assert publication_row is not None and publication_row[0] == 4
        snapshot_assets = publication_row[1]["courses"][0]["modules"][0]["lessons"][0]["assets"]
        snapshot_policy = {item["id"]: item for item in snapshot_assets}
        assert snapshot_policy[str(pdf.asset)]["content_role"] == "support_material"
        assert snapshot_policy[str(pdf.asset)]["download_allowed"] is True
        assert snapshot_policy[str(video.asset)]["content_role"] == "course_content"
        assert connection.execute(
            "select content_role,download_allowed from publication_asset "
            "where seller_organization_id=%s and publication_id=%s and asset_id=%s",
            (graph.seller, publication, pdf.asset),
        ).fetchone() == ("support_material", True)
    set_policy(
        OWNER,
        graph,
        pdf,
        content_role="course_content",
        download_allowed=False,
        expected_version=2,
    )
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select content_role,download_allowed from publication_asset "
            "where seller_organization_id=%s and publication_id=%s and asset_id=%s",
            (graph.seller, publication, pdf.asset),
        ).fetchone() == ("support_material", True)
    entitlement = seed_entitlement(graph, publication)
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "learning.player.read")
        v2_row = connection.execute(
            "select app_private.read_learning_player_v2(%s,%s,%s)",
            (BUYER, entitlement, graph.lesson),
        ).fetchone()
        assert v2_row is not None
        connection.commit()
    v2 = v2_row[0]
    assert all("content_role" not in item for item in v2["selected_assets"])
    assert all("download_allowed" not in item for item in v2["selected_assets"])
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "learning.player.read")
        v3_row = connection.execute(
            "select app_private.read_learning_player_v3(%s,%s,%s)",
            (BUYER, entitlement, graph.lesson),
        ).fetchone()
        assert v3_row is not None
        connection.commit()
    v3 = v3_row[0]
    assert v3["schema_version"] == 3
    player_policy = {item["id"]: item for item in v3["selected_assets"]}
    assert player_policy[str(pdf.asset)]["download_allowed"] is True
    assert player_policy[str(video.asset)]["download_allowed"] is False
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "library.asset.download")
        allowed = connection.execute(
            "select asset_id from app_private.authorize_publication_asset_download(%s,%s,%s)",
            (BUYER, entitlement, pdf.asset),
        ).fetchone()
        assert allowed == (pdf.asset,)
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "library.asset.download")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_publication_asset_download(%s,%s,%s)",
                (BUYER, entitlement, video.asset),
            )

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, OTHER_OWNER, None, "library.asset.download")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_publication_asset_download(%s,%s,%s)",
                (OTHER_OWNER, entitlement, pdf.asset),
            )

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set status='revoked' where id=%s", (entitlement,)
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "library.asset.download")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_publication_asset_download(%s,%s,%s)",
                (BUYER, entitlement, pdf.asset),
            )

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set status='active',"
            "starts_at=clock_timestamp()-interval '2 days',"
            "expires_at=clock_timestamp()-interval '1 day' where id=%s",
            (entitlement,),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "library.asset.download")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_publication_asset_download(%s,%s,%s)",
                (BUYER, entitlement, pdf.asset),
            )

    with psycopg.connect(ADMIN_URL) as connection:
        blocked_graph = create_graph(connection)
        blocked_pdf = add_asset(connection, blocked_graph, media_type="application/pdf", position=0)
        connection.commit()
    set_policy(
        OWNER,
        blocked_graph,
        blocked_pdf,
        content_role="support_material",
        download_allowed=False,
        expected_version=1,
    )
    blocked_publication = publish(blocked_graph, expected_course_version=2)
    blocked_entitlement = seed_entitlement(blocked_graph, blocked_publication)
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, "library.asset.download")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_publication_asset_download(%s,%s,%s)",
                (BUYER, blocked_entitlement, blocked_pdf.asset),
            )


def test_schema_ready_fails_closed_on_tamper() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)
        connection.execute(
            "alter table lesson_asset_draft disable trigger lesson_asset_draft_policy_valid"
        )
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("update alembic_version set version_num='0015_demo_zero_checkout'")
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()
