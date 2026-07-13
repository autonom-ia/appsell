from __future__ import annotations

import hashlib
import hmac
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from appsell_api.reset_demo_tenant import load_manifest, reset_demo

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
MIGRATOR_URL = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

ISSUER = "https://identity.test.autonomia.site"
SUPERADMIN_SUB = "inspection-superadmin"
SUPERADMIN_ID = uuid5(NAMESPACE_URL, f"{ISSUER}\0{SUPERADMIN_SUB}")
ORDINARY_SUB = "inspection-ordinary"
ORDINARY_ID = uuid5(NAMESPACE_URL, f"{ISSUER}\0{ORDINARY_SUB}")
MEMBER_SUB = "inspection-member"
MEMBER_ID = uuid5(NAMESPACE_URL, f"{ISSUER}\0{MEMBER_SUB}")
RACE_MEMBER_FIRST_SUB = "inspection-race-member-first"
RACE_MEMBER_FIRST_ID = uuid5(NAMESPACE_URL, f"{ISSUER}\0{RACE_MEMBER_FIRST_SUB}")
RACE_GRANT_FIRST_SUB = "inspection-race-grant-first"
RACE_GRANT_FIRST_ID = uuid5(NAMESPACE_URL, f"{ISSUER}\0{RACE_GRANT_FIRST_SUB}")


def apply_context(connection: psycopg.Connection[Any], actor: UUID, capability: str) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}::{capability}:{issued}:{expires}"
    signature = hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,null,%s,%s,%s,%s)",
        (actor, capability, issued, expires, signature),
    )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def start_inspection(
    connection: psycopg.Connection[Any], token: str, *, ttl: int = 300
) -> tuple[Any, ...]:
    apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.start")
    row = connection.execute(
        "select * from app_private.start_demo_inspection(%s,%s,%s,%s,%s,%s)",
        (
            SUPERADMIN_ID,
            SUPERADMIN_SUB,
            "Validacao operacional do tenant demo",
            ttl,
            token_hash(token),
            uuid4(),
        ),
    ).fetchone()
    assert row is not None
    return row


@pytest.fixture(scope="module", autouse=True)
def seed_demo_and_platform_identities() -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.execute(
            "insert into identity_user("
            "id,identity_issuer,identity_sub,email_normalized,email_verified) "
            "values(%s,%s,%s,'inspection.superadmin@example.test',true),"
            "(%s,%s,%s,'inspection.ordinary@example.test',true),"
            "(%s,%s,%s,'inspection.member@example.test',true),"
            "(%s,%s,%s,'inspection.race.member@example.test',true),"
            "(%s,%s,%s,'inspection.race.grant@example.test',true) "
            "on conflict(id) do nothing",
            (
                SUPERADMIN_ID,
                ISSUER,
                SUPERADMIN_SUB,
                ORDINARY_ID,
                ISSUER,
                ORDINARY_SUB,
                MEMBER_ID,
                ISSUER,
                MEMBER_SUB,
                RACE_MEMBER_FIRST_ID,
                ISSUER,
                RACE_MEMBER_FIRST_SUB,
                RACE_GRANT_FIRST_ID,
                ISSUER,
                RACE_GRANT_FIRST_SUB,
            ),
        )
        connection.execute(
            "insert into platform_grant(identity_user_id,role,status) "
            "values(%s,'platform_superadmin','active') "
            "on conflict(identity_user_id,role) do update set "
            "status='active',revoked_at=null",
            (SUPERADMIN_ID,),
        )
        connection.commit()


def test_ticket_lifecycle_is_fixed_to_demo_read_only_and_append_only() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    token = "A" * 43
    with psycopg.connect(RUNTIME_URL) as connection:
        started = start_inspection(connection, token)
        inspection_id, _expires_at, seller_id, seller_slug, seller_name = started
        connection.commit()

        apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.read")
        projection = connection.execute(
            "select app_private.read_demo_inspection(%s,%s,%s,%s,%s)",
            (SUPERADMIN_ID, SUPERADMIN_SUB, inspection_id, token_hash(token), uuid4()),
        ).fetchone()
        connection.commit()
        assert projection is not None
        assert projection[0]["seller"] == {
            "id": manifest["seller"]["id"],
            "slug": manifest["seller"]["slug"],
            "display_name": manifest["seller"]["display_name"],
            "status": "active",
        }
        assert projection[0]["product"]["id"] == manifest["product"]["id"]
        assert projection[0]["publication"]["id"] == manifest["publication"]["id"]
        assert projection[0]["courses"] == [
            {"id": manifest["course"]["id"], "title": manifest["course"]["title"], "position": 0}
        ]
        assert "description" not in projection[0]["product"]
        assert "snapshot" not in projection[0]
        assert "assets" not in projection[0]

        apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.revoke")
        revoked = connection.execute(
            "select app_private.revoke_demo_inspection(%s,%s,%s,%s,%s,%s)",
            (
                SUPERADMIN_ID,
                SUPERADMIN_SUB,
                inspection_id,
                token_hash(token),
                "Inspecao concluida pelo operador",
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
        assert revoked is not None and revoked[0]["status"] == "revoked"

        apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.read")
        assert connection.execute(
            "select app_private.read_demo_inspection(%s,%s,%s,%s,%s)",
            (SUPERADMIN_ID, SUPERADMIN_SUB, inspection_id, token_hash(token), uuid4()),
        ).fetchone() == (None,)

    assert str(seller_id) == manifest["seller"]["id"]
    assert seller_slug == manifest["seller"]["slug"]
    assert seller_name == manifest["seller"]["display_name"]
    with psycopg.connect(ADMIN_URL) as connection:
        stored = connection.execute(
            "select token_sha256,state,reason from app_private.demo_inspection_ticket where id=%s",
            (inspection_id,),
        ).fetchone()
        memberships = connection.execute(
            "select count(*) from seller_membership where identity_user_id=%s",
            (SUPERADMIN_ID,),
        ).fetchone()
        actions = connection.execute(
            "select action,actor_identity_user_id,seller_organization_id,request_id,metadata "
            "from audit_event where target_type='demo_inspection' "
            "and target_id=%s order by created_at",
            (inspection_id,),
        ).fetchall()
        assert stored == (
            token_hash(token),
            "revoked",
            "Validacao operacional do tenant demo",
        )
        assert token not in str(stored)
        assert memberships == (0,)
        assert [row[0] for row in actions] == [
            "platform.demo_inspection.started",
            "platform.demo_inspection.read",
            "platform.demo_inspection.revoked",
        ]
        assert all(row[1] == SUPERADMIN_ID for row in actions)
        assert all(str(row[2]) == manifest["seller"]["id"] for row in actions)
        assert all(row[3] is not None for row in actions)
        assert actions[0][4] == {
            "reason": "Validacao operacional do tenant demo",
            "ttl_seconds": 300,
        }
        assert actions[1][4] == {"projection": "published_demo_metadata"}
        assert actions[2][4] == {"reason": "Inspecao concluida pelo operador"}
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "update audit_event set metadata='{}' where target_id=%s", (inspection_id,)
            )


def test_wrong_token_subject_capability_and_revoked_grant_fail_closed() -> None:
    assert ADMIN_URL and RUNTIME_URL
    token = "B" * 43
    with psycopg.connect(RUNTIME_URL) as connection:
        inspection_id = start_inspection(connection, token)[0]
        connection.commit()
        apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.read")
        assert connection.execute(
            "select app_private.read_demo_inspection(%s,%s,%s,%s,%s)",
            (SUPERADMIN_ID, SUPERADMIN_SUB, inspection_id, token_hash("C" * 43), uuid4()),
        ).fetchone() == (None,)
        connection.rollback()

        apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_demo_inspection(%s,%s,%s,%s,%s)",
                (SUPERADMIN_ID, ORDINARY_SUB, inspection_id, token_hash(token), uuid4()),
            )
        connection.rollback()

        apply_context(connection, SUPERADMIN_ID, "seller.list")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_demo_inspection(%s,%s,%s,%s,%s)",
                (SUPERADMIN_ID, SUPERADMIN_SUB, inspection_id, token_hash(token), uuid4()),
            )

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update platform_grant set status='revoked',revoked_at=clock_timestamp() "
            "where identity_user_id=%s and role='platform_superadmin'",
            (SUPERADMIN_ID,),
        )
        connection.commit()
    try:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.read")
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(
                    "select app_private.read_demo_inspection(%s,%s,%s,%s,%s)",
                    (SUPERADMIN_ID, SUPERADMIN_SUB, inspection_id, token_hash(token), uuid4()),
                )
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                "update platform_grant set status='active',revoked_at=null "
                "where identity_user_id=%s and role='platform_superadmin'",
                (SUPERADMIN_ID,),
            )
            connection.commit()


def test_rotation_and_expiration_close_old_ticket_and_are_audited() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        first_id = start_inspection(connection, "D" * 43)[0]
        connection.commit()
        second_id = start_inspection(connection, "E" * 43)[0]
        connection.commit()
    assert first_id != second_id
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select state,revoked_reason from app_private.demo_inspection_ticket where id=%s",
            (first_id,),
        ).fetchone() == ("revoked", "Rotated by a new inspection request")
        connection.execute(
            "update app_private.demo_inspection_ticket set "
            "started_at=clock_timestamp()-interval '2 minutes',"
            "expires_at=clock_timestamp()-interval '1 minute' where id=%s",
            (second_id,),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SUPERADMIN_ID, "platform.demo.inspect.read")
        assert connection.execute(
            "select app_private.read_demo_inspection(%s,%s,%s,%s,%s)",
            (SUPERADMIN_ID, SUPERADMIN_SUB, second_id, token_hash("E" * 43), uuid4()),
        ).fetchone() == (None,)
        connection.commit()
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select state from app_private.demo_inspection_ticket where id=%s", (second_id,)
        ).fetchone() == ("expired",)
        actions = connection.execute(
            "select action from audit_event where target_id in (%s,%s) order by created_at",
            (first_id, second_id),
        ).fetchall()
    assert ("platform.demo_inspection.rotated",) in actions
    assert ("platform.demo_inspection.expired",) in actions


def test_superadmin_and_seller_membership_are_mutually_exclusive_in_both_orders() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    seller_id = UUID(manifest["seller"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "insert into seller_membership("
                "seller_organization_id,identity_user_id,role,status) "
                "values(%s,%s,'seller_admin','active')",
                (seller_id, SUPERADMIN_ID),
            )
        connection.rollback()
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_admin','active')",
            (seller_id, MEMBER_ID),
        )
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "insert into platform_grant(identity_user_id,role,status) "
                "values(%s,'platform_superadmin','active')",
                (MEMBER_ID,),
            )
        connection.rollback()
        assert connection.execute(
            "select count(*) from seller_membership where identity_user_id in (%s,%s)",
            (SUPERADMIN_ID, MEMBER_ID),
        ).fetchone() == (0,)


def test_runtime_has_only_function_execution_and_no_ticket_or_tenant_writes() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        privileges = connection.execute(
            """
            select
              has_table_privilege('appsell_runtime','app_private.demo_inspection_ticket','SELECT'),
              has_table_privilege('appsell_runtime','app_private.demo_inspection_ticket','INSERT'),
              has_function_privilege(
                'appsell_runtime',
                'app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)',
                'EXECUTE'
              ),
              has_table_privilege('appsell_runtime','seller_membership','INSERT'),
              has_table_privilege('appsell_runtime','catalog_product','UPDATE')
            """
        ).fetchone()
        policies = connection.execute(
            "select count(*) from pg_policies where schemaname='app_private' "
            "and tablename='demo_inspection_ticket'"
        ).fetchone()
    assert privileges == (False, False, True, False, False)
    assert policies == (0,)


def test_demo_reset_serializes_after_concurrent_start_and_revokes_ticket() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, manifest_sha = load_manifest()
    token = "F" * 43
    reset_entered = Event()

    def reset_in_parallel() -> None:
        reset_entered.set()
        with psycopg.connect(ADMIN_URL) as reset_connection:
            reset_demo(reset_connection, manifest, manifest_sha, ISSUER)
            reset_connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        connection.execute("select pg_advisory_xact_lock(hashtextextended('autonomia-demo-v1',0))")
        inspection_id = start_inspection(connection, token)[0]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(reset_in_parallel)
            assert reset_entered.wait(timeout=2)
            time.sleep(0.1)
            assert not future.done()
            connection.commit()
            future.result(timeout=10)

    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select state,revoked_reason from app_private.demo_inspection_ticket where id=%s",
            (inspection_id,),
        ).fetchone() == ("revoked", "Revoked by isolated demo tenant reset")
        assert connection.execute(
            "select count(*) from app_private.demo_inspection_ticket "
            "where seller_organization_id=%s and state='active'",
            (UUID(manifest["seller"]["id"]),),
        ).fetchone() == (0,)


def test_concurrent_grant_revocation_prevents_inspection_start() -> None:
    assert ADMIN_URL and RUNTIME_URL
    token = "G" * 43
    start_entered = Event()

    def attempt_start() -> bool:
        start_entered.set()
        try:
            with psycopg.connect(RUNTIME_URL) as runtime_connection:
                start_inspection(runtime_connection, token)
                runtime_connection.commit()
            return True
        except psycopg.errors.InvalidAuthorizationSpecification:
            return False

    with psycopg.connect(ADMIN_URL) as admin_connection:
        admin_connection.execute(
            "update platform_grant set status='revoked',revoked_at=clock_timestamp() "
            "where identity_user_id=%s and role='platform_superadmin'",
            (SUPERADMIN_ID,),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(attempt_start)
            assert start_entered.wait(timeout=2)
            time.sleep(0.1)
            assert not future.done()
            admin_connection.commit()
            assert future.result(timeout=10) is False
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select count(*) from app_private.demo_inspection_ticket where token_sha256=%s",
            (token_hash(token),),
        ).fetchone() == (0,)
        connection.execute(
            "update platform_grant set status='active',revoked_at=null "
            "where identity_user_id=%s and role='platform_superadmin'",
            (SUPERADMIN_ID,),
        )
        connection.commit()


def test_concurrent_membership_and_superadmin_grant_never_both_commit() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    seller_id = UUID(manifest["seller"]["id"])

    def insert_grant(actor: UUID, entered: Event) -> bool:
        entered.set()
        try:
            with psycopg.connect(ADMIN_URL) as connection:
                connection.execute(
                    "insert into platform_grant(identity_user_id,role,status) "
                    "values(%s,'platform_superadmin','active')",
                    (actor,),
                )
                connection.commit()
            return True
        except psycopg.errors.InvalidAuthorizationSpecification:
            return False

    def insert_membership(actor: UUID, entered: Event) -> bool:
        entered.set()
        try:
            with psycopg.connect(ADMIN_URL) as connection:
                connection.execute(
                    "insert into seller_membership("
                    "seller_organization_id,identity_user_id,role,status) "
                    "values(%s,%s,'seller_admin','active')",
                    (seller_id, actor),
                )
                connection.commit()
            return True
        except psycopg.errors.InvalidAuthorizationSpecification:
            return False

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into seller_membership("
            "seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_admin','active')",
            (seller_id, RACE_MEMBER_FIRST_ID),
        )
        entered = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert_grant, RACE_MEMBER_FIRST_ID, entered)
            assert entered.wait(timeout=2)
            time.sleep(0.1)
            assert not future.done()
            connection.commit()
            assert future.result(timeout=10) is False

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into platform_grant(identity_user_id,role,status) "
            "values(%s,'platform_superadmin','active')",
            (RACE_GRANT_FIRST_ID,),
        )
        entered = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert_membership, RACE_GRANT_FIRST_ID, entered)
            assert entered.wait(timeout=2)
            time.sleep(0.1)
            assert not future.done()
            connection.commit()
            assert future.result(timeout=10) is False

    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from seller_membership
                where identity_user_id=%s and status='active'),
              (select count(*) from platform_grant
                where identity_user_id=%s and role='platform_superadmin' and status='active'),
              (select count(*) from seller_membership
                where identity_user_id=%s and status='active'),
              (select count(*) from platform_grant
                where identity_user_id=%s and role='platform_superadmin' and status='active')
            """,
            (
                RACE_MEMBER_FIRST_ID,
                RACE_MEMBER_FIRST_ID,
                RACE_GRANT_FIRST_ID,
                RACE_GRANT_FIRST_ID,
            ),
        ).fetchone()
    assert state == (1, 0, 0, 1)


@pytest.mark.skipif(not MIGRATOR_URL, reason="migrator URL is required for upgrade gate")
def test_upgrade_rejects_existing_active_superadmin_membership_contamination() -> None:
    assert ADMIN_URL and MIGRATOR_URL
    database_name = f"appsell_inspection_upgrade_{uuid4().hex}"
    maintenance_url = make_conninfo(ADMIN_URL, dbname="postgres")
    target_url = make_conninfo(ADMIN_URL, dbname=database_name)
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
            sql.SQL("grant connect on database {} to appsell_migrator,appsell_runtime").format(
                sql.Identifier(database_name)
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
                "0006_demo_learning",
            ],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        actor, seller = uuid4(), uuid4()
        with psycopg.connect(target_url) as connection:
            connection.execute(
                "insert into identity_user("
                "id,identity_issuer,identity_sub,email_normalized,email_verified) "
                "values(%s,%s,'contaminated-superadmin','contaminated@example.test',true)",
                (actor, ISSUER),
            )
            connection.execute(
                "insert into seller_organization(id,slug,display_name,status) "
                "values(%s,'contaminated-seller','Contaminated Seller','active')",
                (seller,),
            )
            connection.execute(
                "insert into seller_membership("
                "seller_organization_id,identity_user_id,role,status) "
                "values(%s,%s,'seller_owner','active')",
                (seller, actor),
            )
            connection.execute(
                "insert into platform_grant(identity_user_id,role,status) "
                "values(%s,'platform_superadmin','active')",
                (actor,),
            )
            connection.commit()
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
            cwd=repository_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert upgrade.returncode != 0
        assert "0007 rejects active superadmin seller memberships" in (
            upgrade.stdout + upgrade.stderr
        )
        with psycopg.connect(target_url) as connection:
            assert connection.execute("select version_num from alembic_version").fetchone() == (
                "0006_demo_learning",
            )
            assert connection.execute(
                "select to_regclass('app_private.demo_inspection_ticket')"
            ).fetchone() == (None,)
    finally:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("drop database if exists {} with (force)").format(
                    sql.Identifier(database_name)
                )
            )
