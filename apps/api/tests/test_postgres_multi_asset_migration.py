from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

import appsell_api.reset_demo_tenant as reset_module

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
MIGRATOR_URL = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL")
ISSUER = "https://identity.test.autonomia.site"

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, MIGRATOR_URL)),
    reason="isolated PostgreSQL migrator environment is required",
)


def insert_publication_asset(
    connection: psycopg.Connection[Any],
    *,
    seller: UUID,
    product: UUID,
    publication: UUID,
    course: UUID,
    module: UUID,
    lesson: UUID,
    actor: UUID,
    position: int,
) -> None:
    asset = uuid4()
    blob = uuid4()
    file_name = f"migration-limit-{position}.pdf"
    object_key = f"migration-asset-limit/{blob}.pdf"
    sha256 = hashlib.sha256(object_key.encode()).hexdigest()
    title = f"Material migration {position}"
    connection.execute(
        "insert into asset_record(seller_organization_id,id,catalog_product_id,title) "
        "values(%s,%s,%s,%s)",
        (seller, asset, product, title),
    )
    connection.execute(
        """
        insert into asset_blob(
          seller_organization_id,id,storage_driver,storage_bucket,object_key,
          file_name,media_type,byte_size,sha256,magic_type,scanner_result,verified_at
        ) values(%s,%s,'legacy_bundle','migration-limit',%s,%s,'application/pdf',1,%s,
          'application/pdf','legacy_trusted',clock_timestamp())
        """,
        (seller, blob, object_key, file_name, sha256),
    )
    connection.execute(
        "insert into asset_version(seller_organization_id,asset_id,version,blob_id,"
        "actor_identity_user_id) values(%s,%s,1,%s,%s)",
        (seller, asset, blob, actor),
    )
    connection.execute(
        """
        insert into publication_asset(
          seller_organization_id,catalog_product_id,publication_id,course_id,module_id,
          lesson_id,asset_id,asset_version,blob_id,position,title,file_name,media_type,
          byte_size,sha256,storage_driver,storage_bucket,object_key
        ) values(%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,'application/pdf',1,%s,
          'legacy_bundle','migration-limit',%s)
        """,
        (
            seller,
            product,
            publication,
            course,
            module,
            lesson,
            asset,
            blob,
            position,
            title,
            file_name,
            sha256,
            object_key,
        ),
    )


def seed_pre_0014_publication(
    connection: psycopg.Connection[Any],
) -> tuple[UUID, UUID, UUID, UUID, UUID, UUID, UUID]:
    """Seed only the 0013 graph required to exercise the 0014 migration."""
    seller, product, publication = uuid4(), uuid4(), uuid4()
    course, module, lesson = uuid4(), uuid4(), uuid4()
    actor = uuid5(NAMESPACE_URL, f"{ISSUER}\0migration-0014-actor")
    suffix = uuid4().hex[:12]
    connection.execute(
        """
        insert into identity_user(
          id,identity_issuer,identity_sub,email_normalized,email_verified
        ) values(%s,%s,%s,%s,true)
        """,
        (actor, ISSUER, f"migration-0014-{suffix}", f"migration-{suffix}@example.test"),
    )
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) values(%s,%s,%s,'active')",
        (seller, f"migration-{suffix}", f"Migration {suffix}"),
    )
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title) "
        "values(%s,%s,%s,'Migration product')",
        (seller, product, f"product-{suffix}"),
    )
    connection.execute(
        "insert into course(seller_organization_id,id,title) values(%s,%s,'Migration course')",
        (seller, course),
    )
    connection.execute(
        "insert into product_course("
        "seller_organization_id,catalog_product_id,course_id,position"
        ") values(%s,%s,%s,0)",
        (seller, product, course),
    )
    connection.execute(
        "insert into course_module(seller_organization_id,id,course_id,title,position) "
        "values(%s,%s,%s,'Migration module',0)",
        (seller, module, course),
    )
    connection.execute(
        "insert into lesson("
        "seller_organization_id,id,course_id,module_id,title,position"
        ") values(%s,%s,%s,%s,'Migration lesson',0)",
        (seller, lesson, course, module),
    )
    connection.execute(
        """
        insert into publication(
          seller_organization_id,id,catalog_product_id,version,schema_version,
          source_sha256,snapshot,actor_identity_user_id
        ) values(
          %s,%s,%s,1,2,repeat('0',64),
          jsonb_build_object(
            'schema_version',2,
            'seller_organization_id',%s::text,
            'product',jsonb_build_object('id',%s::text),
            'courses',jsonb_build_array(jsonb_build_object(
              'id',%s::text,'position',0,
              'modules',jsonb_build_array(jsonb_build_object(
                'id',%s::text,'position',0,
                'lessons',jsonb_build_array(jsonb_build_object(
                  'id',%s::text,'position',0
                ))
              ))
            ))
          ),%s
        )
        """,
        (
            seller,
            publication,
            product,
            seller,
            product,
            course,
            module,
            lesson,
            actor,
        ),
    )
    return seller, product, publication, course, module, lesson, actor


@pytest.mark.skipif(not MIGRATOR_URL, reason="migrator URL is required for upgrade gate")
def test_0014_upgrade_blocks_concurrent_101st_asset() -> None:
    assert ADMIN_URL and MIGRATOR_URL
    database_name = f"appsell_multi_asset_upgrade_{uuid4().hex}"
    maintenance_url = make_conninfo(ADMIN_URL, dbname="postgres")
    target_url = make_conninfo(ADMIN_URL, dbname=database_name)
    migrator_url = (
        make_url(MIGRATOR_URL).set(database=database_name).render_as_string(hide_password=False)
    )
    migrator_role = make_url(MIGRATOR_URL).username
    assert migrator_role
    repository_root = Path(__file__).resolve().parents[3]
    with psycopg.connect(maintenance_url, autocommit=True) as connection:
        connection.execute(sql.SQL("create database {}").format(sql.Identifier(database_name)))
        connection.execute(
            sql.SQL("alter database {} owner to appsell_owner").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("comment on database {} is {}").format(
                sql.Identifier(database_name),
                sql.Literal(reset_module.DEMO_DATABASE_MARKER),
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
                "0013_private_media",
            ],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        with psycopg.connect(target_url) as connection:
            seller, product, publication, course, module, lesson, actor = seed_pre_0014_publication(
                connection
            )
            for position in range(100):
                insert_publication_asset(
                    connection,
                    seller=seller,
                    product=product,
                    publication=publication,
                    course=course,
                    module=module,
                    lesson=lesson,
                    actor=actor,
                    position=1_000 + position,
                )
            connection.commit()

        inserter = psycopg.connect(target_url)
        try:
            insert_publication_asset(
                inserter,
                seller=seller,
                product=product,
                publication=publication,
                course=course,
                module=module,
                lesson=lesson,
                actor=actor,
                position=2_000,
            )

            def upgrade() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "alembic",
                        "-c",
                        "apps/api/alembic.ini",
                        "upgrade",
                        "0014_multi_asset_player",
                    ],
                    cwd=repository_root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(upgrade)
                time.sleep(0.25)
                assert not future.done(), "0014 did not wait for the concurrent INSERT"
                inserter.commit()
                result = future.result(timeout=35)
            assert result.returncode != 0
            assert "publication lesson exceeds the 100 asset contract" in (
                result.stdout + result.stderr
            )
        finally:
            inserter.close()

        with psycopg.connect(target_url) as connection:
            assert connection.execute("select version_num from alembic_version").fetchone() == (
                "0013_private_media",
            )
            assert connection.execute(
                "select count(*) from publication_asset where seller_organization_id=%s "
                "and publication_id=%s and lesson_id=%s",
                (seller, publication, lesson),
            ).fetchone() == (101,)
            assert connection.execute(
                "select count(*) from pg_trigger "
                "where tgrelid='public.publication_asset'::regclass "
                "and tgname='publication_lesson_asset_limit' and not tgisinternal"
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("drop database if exists {} with (force)").format(
                    sql.Identifier(database_name)
                )
            )
