from __future__ import annotations

import hashlib
import hmac
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest
from psycopg.types.json import Jsonb

from appsell_api.reset_demo_tenant import load_manifest, reset_demo, snapshot_for

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
ISSUER = "https://identity.demo.test.autonomia.site"
FOUNDATION_DATABASE_WORKERS = 12

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)


def identity_id(subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{ISSUER}\0{subject}")


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


def seed_learning_entitlement(
    connection: psycopg.Connection[Any], manifest: dict[str, Any]
) -> None:
    connection.execute(
        """
        insert into buyer_entitlement(
          seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
          publication_id,reference_key,source,status,progress_percent
        ) values(%s,%s,%s,%s,%s,'pytest-progress-fixture','demo_seed','active',0)
        """,
        (
            UUID(manifest["seller"]["id"]),
            UUID(manifest["entitlement"]["id"]),
            identity_id(manifest["personas"]["buyer"]["subject"]),
            UUID(manifest["product"]["id"]),
            UUID(manifest["publication"]["id"]),
        ),
    )


@pytest.fixture(autouse=True)
def pristine_demo() -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        seed_learning_entitlement(connection, manifest)
        connection.commit()


def test_projection_pin_rls_and_player_are_bound_to_the_buyer() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    publication = UUID(manifest["publication"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select version_num from alembic_version),
              (select publication_id from buyer_entitlement where id=%s),
              (select count(*) from publication_course where publication_id=%s),
              (select count(*) from publication_lesson where publication_id=%s),
              has_table_privilege('appsell_runtime','lesson_progress','INSERT'),
              has_table_privilege('appsell_runtime','lesson_progress','SELECT'),
              has_table_privilege('appsell_runtime','course_learning_state','UPDATE'),
              has_table_privilege('appsell_runtime','course_learning_state','SELECT'),
              has_table_privilege(
                'appsell_runtime','app_private.learning_progress_request','SELECT'
              ),
              has_function_privilege(
                'appsell_runtime',
                'app_private.read_learning_player_v2(uuid,uuid,uuid)',
                'EXECUTE'
              )
            """,
            (entitlement, publication, publication),
        ).fetchone()
    assert state == (
        "0038_player_hides_materials",
        publication,
        1,
        5,
        False,
        False,
        False,
        False,
        False,
        True,
    )

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.player.read")
        player = connection.execute(
            "select app_private.read_learning_player(%s,%s,null)",
            (buyer, entitlement),
        ).fetchone()
        connection.rollback()
        apply_context(connection, buyer, "learning.player.read")
        final_player = connection.execute(
            "select app_private.read_learning_player_v2(%s,%s,%s)",
            (buyer, entitlement, UUID(manifest["video"]["lesson_id"])),
        ).fetchone()
        connection.rollback()
        apply_context(connection, admin, "learning.player.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_learning_player(%s,%s,null)",
                (admin, entitlement),
            )
    assert player is not None
    assert player[0]["publication"]["id"] == manifest["publication"]["id"]
    assert player[0]["selected_lesson_id"] == manifest["lessons"][0]["lesson_id"]
    assert player[0]["selected_asset"]["id"] == manifest["lessons"][0]["asset_id"]
    assert "selected_assets" not in player[0]
    assert final_player is not None
    final_assets = final_player[0]["selected_assets"]
    assert [asset["media_type"] for asset in final_assets] == ["video/mp4", "application/pdf"]
    assert {asset["id"] for asset in final_assets} == {
        manifest["video"]["asset_id"],
        manifest["lessons"][-1]["asset_id"],
    }
    assert final_player[0]["selected_asset"] == final_assets[0]
    course_lessons = [
        lesson for module in player[0]["course"]["modules"] for lesson in module["lessons"]
    ]
    assert course_lessons[0]["content"] == manifest["lessons"][0]["summary"]
    assert all(lesson["content"] == "" for lesson in course_lessons[1:])


def test_publication_rejects_a_101st_asset_for_one_lesson() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    publication = UUID(manifest["publication"]["id"])
    course = UUID(manifest["course"]["id"])
    module = UUID(manifest["module"]["id"])
    lesson = UUID(manifest["video"]["lesson_id"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])

    def insert_asset(connection: psycopg.Connection[Any], position: int) -> None:
        asset = uuid4()
        blob = uuid4()
        file_name = f"limit-{position}.pdf"
        object_key = f"asset-limit/{blob}.pdf"
        sha256 = hashlib.sha256(object_key.encode()).hexdigest()
        connection.execute(
            "insert into asset_record(seller_organization_id,id,catalog_product_id,title) "
            "values(%s,%s,%s,%s)",
            (seller, asset, product, f"Material limite {position}"),
        )
        connection.execute(
            """
            insert into asset_blob(
              seller_organization_id,id,storage_driver,storage_bucket,object_key,
              file_name,media_type,byte_size,sha256,magic_type,scanner_result,verified_at
            ) values(%s,%s,'legacy_bundle','asset-limit',%s,%s,'application/pdf',1,%s,
              'application/pdf','legacy_trusted',clock_timestamp())
            """,
            (seller, blob, object_key, file_name, sha256),
        )
        connection.execute(
            "insert into asset_version(seller_organization_id,asset_id,version,blob_id,"
            "actor_identity_user_id) values(%s,%s,1,%s,%s)",
            (seller, asset, blob, admin),
        )
        connection.execute(
            """
            insert into publication_asset(
              seller_organization_id,catalog_product_id,publication_id,course_id,module_id,
              lesson_id,asset_id,asset_version,blob_id,position,title,file_name,media_type,
              byte_size,sha256,storage_driver,storage_bucket,object_key
            ) values(%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,'application/pdf',1,%s,
              'legacy_bundle','asset-limit',%s)
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
                f"Material limite {position}",
                file_name,
                sha256,
                object_key,
            ),
        )

    with psycopg.connect(ADMIN_URL) as connection:
        existing = connection.execute(
            "select count(*) from publication_asset where seller_organization_id=%s "
            "and publication_id=%s and lesson_id=%s",
            (seller, publication, lesson),
        ).fetchone()
        assert existing is not None and existing[0] == 2
        for position in range(existing[0], 100):
            insert_asset(connection, 1_000 + position)
        connection.commit()
        count = connection.execute(
            "select count(*) from publication_asset where seller_organization_id=%s "
            "and publication_id=%s and lesson_id=%s",
            (seller, publication, lesson),
        ).fetchone()
        assert count == (100,)
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_asset(connection, 2_000)


def test_learning_context_is_cleared_before_connection_reuse() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.player.read")
        assert (
            connection.execute(
                "select app_private.read_learning_player(%s,%s,null)",
                (buyer, entitlement),
            ).fetchone()
            is not None
        )
        connection.commit()

        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_learning_player(%s,%s,null)",
                (buyer, entitlement),
            )
        connection.rollback()

        apply_context(connection, admin, "learning.player.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_learning_player(%s,%s,null)",
                (buyer, entitlement),
            )


def test_progress_is_monotonic_idempotent_and_revocation_precedes_replay() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    idempotency = uuid4()
    body_hash = hashlib.sha256(b"first-progress").hexdigest()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.progress.write")
        first = connection.execute(
            "select app_private.record_lesson_progress(%s,%s,%s,25::smallint,0,%s,%s,%s)",
            (buyer, entitlement, lesson, idempotency, body_hash, uuid4()),
        ).fetchone()
        connection.commit()
        apply_context(connection, buyer, "learning.progress.write")
        replay = connection.execute(
            "select app_private.record_lesson_progress(%s,%s,%s,25::smallint,0,%s,%s,%s)",
            (buyer, entitlement, lesson, idempotency, body_hash, uuid4()),
        ).fetchone()
        connection.commit()
        apply_context(connection, buyer, "learning.progress.write")
        lower = connection.execute(
            "select app_private.record_lesson_progress(%s,%s,%s,1::smallint,1,%s,%s,%s)",
            (
                buyer,
                entitlement,
                lesson,
                uuid4(),
                hashlib.sha256(b"lower-progress").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
    assert first is not None and first[0]["applied"] is True
    assert first[0]["lesson_progress_percent"] == 25
    assert replay is not None and replay[0]["applied"] is False
    assert replay[0]["state_version"] == 1
    assert lower is not None and lower[0]["lesson_progress_percent"] == 25
    assert lower[0]["state_version"] == 2

    next_lesson = UUID(manifest["lessons"][1]["lesson_id"])
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.progress.write")
        completed = connection.execute(
            "select app_private.record_lesson_progress(%s,%s,%s,100::smallint,2,%s,%s,%s)",
            (
                buyer,
                entitlement,
                lesson,
                uuid4(),
                hashlib.sha256(b"complete-first").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
        apply_context(connection, buyer, "learning.progress.write")
        completed_downgrade = connection.execute(
            "select app_private.record_lesson_progress(%s,%s,%s,1::smallint,3,%s,%s,%s)",
            (
                buyer,
                entitlement,
                lesson,
                uuid4(),
                hashlib.sha256(b"completed-downgrade").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
        apply_context(connection, buyer, "learning.player.read")
        resumed = connection.execute(
            "select app_private.read_learning_player(%s,%s,null)",
            (buyer, entitlement),
        ).fetchone()
    assert completed is not None and completed[0]["resume_lesson_id"] == str(next_lesson)
    assert completed_downgrade is not None
    assert completed_downgrade[0]["lesson_progress_percent"] == 100
    assert completed_downgrade[0]["resume_lesson_id"] == str(next_lesson)
    assert resumed is not None and resumed[0]["selected_lesson_id"] == str(next_lesson)
    assert resumed[0]["resume_lesson_id"] == str(next_lesson)

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set status='revoked' where id=%s", (entitlement,)
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.progress.write")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.record_lesson_progress(%s,%s,%s,25::smallint,0,%s,%s,%s)",
                (buyer, entitlement, lesson, idempotency, body_hash, uuid4()),
            )


def test_player_rejects_cross_buyer_cross_entitlement_and_cross_lesson() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    other = identity_id("learning-idor-other")
    entitlement = UUID(manifest["entitlement"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified) values(%s,%s,'learning-idor-other',"
            "'learning-idor-other@autonomia.site',true) on conflict(id) do nothing",
            (other, ISSUER),
        )
        connection.commit()

    cases = (
        (other, entitlement, None),
        (buyer, uuid4(), None),
        (buyer, entitlement, uuid4()),
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        for actor, candidate_entitlement, lesson in cases:
            apply_context(connection, actor, "learning.player.read")
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(
                    "select app_private.read_learning_player(%s,%s,%s)",
                    (actor, candidate_entitlement, lesson),
                )
            connection.rollback()


def test_entitlement_remains_pinned_after_republication_and_draft_edit() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    entitlement = UUID(manifest["entitlement"]["id"])
    baseline = UUID(manifest["publication"]["id"])
    replacement = uuid4()
    replacement_snapshot = snapshot_for(manifest)
    replacement_snapshot["product"]["title"] = "Published replacement"
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update catalog_product set title='Unpublished draft title' "
            "where seller_organization_id=%s and id=%s",
            (seller, product),
        )
        connection.execute(
            """
            with payload as (select %s::jsonb snapshot)
            insert into publication(
              seller_organization_id,id,catalog_product_id,version,schema_version,
              source_sha256,snapshot,actor_identity_user_id
            ) select %s,%s,%s,2,2,
              encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),snapshot,%s
            from payload
            """,
            (Jsonb(replacement_snapshot), seller, replacement, product, admin),
        )
        connection.execute(
            "update catalog_product set status='published',published_snapshot_id=%s "
            "where seller_organization_id=%s and id=%s",
            (replacement, seller, product),
        )
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.player.read")
        player = connection.execute(
            "select app_private.read_learning_player(%s,%s,null)", (buyer, entitlement)
        ).fetchone()
        connection.rollback()
        apply_context(connection, buyer, "library.read")
        library = connection.execute(
            "select app_private.read_buyer_library(%s)", (buyer,)
        ).fetchone()
    assert player is not None and library is not None
    assert player[0]["publication"]["id"] == str(baseline)
    assert player[0]["product"]["title"] == manifest["product"]["title"]
    assert library[0][0]["product"]["publication_id"] == str(baseline)
    assert library[0][0]["product"]["title"] == manifest["product"]["title"]


def test_multi_course_defaults_to_first_then_resumes_last_active_course() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    buyer = identity_id("multi-course-buyer")
    course = uuid4()
    module = uuid4()
    lesson = uuid4()
    publication = uuid4()
    entitlement = uuid4()
    snapshot = snapshot_for(manifest)
    snapshot["courses"].append(
        {
            "id": str(course),
            "title": "Second course",
            "description": "Second course snapshot",
            "position": 1,
            "modules": [
                {
                    "id": str(module),
                    "title": "Second module",
                    "status": "active",
                    "position": 0,
                    "lessons": [
                        {
                            "id": str(lesson),
                            "title": "Second lesson",
                            "kind": "text",
                            "content": "Second lesson snapshot",
                            "status": "active",
                            "position": 0,
                        }
                    ],
                }
            ],
        }
    )
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified) values(%s,%s,'multi-course-buyer',"
            "'multi-course-buyer@autonomia.site',true) on conflict(id) do nothing",
            (buyer, ISSUER),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status) "
            "values(%s,%s,'Second course','Second course snapshot','published')",
            (seller, course),
        )
        connection.execute(
            "insert into product_course("
            "seller_organization_id,catalog_product_id,course_id,position) "
            "values(%s,%s,%s,1)",
            (seller, product, course),
        )
        connection.execute(
            "insert into course_module(seller_organization_id,id,course_id,title,status,position) "
            "values(%s,%s,%s,'Second module','active',0)",
            (seller, module, course),
        )
        connection.execute(
            "insert into lesson(seller_organization_id,id,course_id,module_id,title,body_text,"
            "status,position) values(%s,%s,%s,%s,'Second lesson','Second lesson snapshot',"
            "'active',0)",
            (seller, lesson, course, module),
        )
        connection.execute(
            """
            with payload as (select %s::jsonb snapshot)
            insert into publication(
              seller_organization_id,id,catalog_product_id,version,schema_version,
              source_sha256,snapshot,actor_identity_user_id
            ) select %s,%s,%s,2,2,
              encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),snapshot,%s
            from payload
            """,
            (Jsonb(snapshot), seller, publication, product, admin),
        )
        connection.execute(
            "insert into buyer_entitlement(seller_organization_id,id,buyer_identity_user_id,"
            "catalog_product_id,publication_id,reference_key,source,status) "
            "values(%s,%s,%s,%s,%s,%s,'manual','active')",
            (seller, entitlement, buyer, product, publication, f"multi-{entitlement}"),
        )
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.player.read")
        initial = connection.execute(
            "select app_private.read_learning_player(%s,%s,null)", (buyer, entitlement)
        ).fetchone()
        connection.commit()
        apply_context(connection, buyer, "learning.progress.write")
        progressed = connection.execute(
            "select app_private.record_lesson_progress(%s,%s,%s,10::smallint,0,%s,%s,%s)",
            (
                buyer,
                entitlement,
                lesson,
                uuid4(),
                hashlib.sha256(b"multi-course-progress").hexdigest(),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
        apply_context(connection, buyer, "learning.player.read")
        resumed = connection.execute(
            "select app_private.read_learning_player(%s,%s,null)", (buyer, entitlement)
        ).fetchone()
    assert initial is not None and progressed is not None and resumed is not None
    assert initial[0]["selected_lesson_id"] == manifest["lessons"][0]["lesson_id"]
    assert resumed[0]["selected_lesson_id"] == str(lesson)
    assert resumed[0]["course"]["id"] == str(course)


def test_publication_projections_are_append_only() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    publication = UUID(manifest["publication"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(psycopg.Error) as update_error:
            connection.execute(
                "update publication_course set lesson_count=0 where publication_id=%s",
                (publication,),
            )
        assert update_error.value.sqlstate == "55000"
        connection.rollback()
        with pytest.raises(psycopg.Error) as delete_error:
            connection.execute(
                "delete from publication_lesson where publication_id=%s", (publication,)
            )
        assert delete_error.value.sqlstate == "55000"


def test_same_key_concurrency_applies_once_and_replays_identically() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    key = uuid4()
    body_hash = hashlib.sha256(b"same-key-progress").hexdigest()

    def update(_: int) -> tuple[bool, int, int]:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, buyer, "learning.progress.write")
            row = connection.execute(
                "select app_private.record_lesson_progress(%s,%s,%s,40::smallint,0,%s,%s,%s)",
                (buyer, entitlement, lesson, key, body_hash, uuid4()),
            ).fetchone()
            assert row is not None
            return row[0]["applied"], row[0]["lesson_progress_percent"], row[0]["state_version"]

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(update, range(12)))
    assert [result[0] for result in results].count(True) == 1
    assert [result[0] for result in results].count(False) == 11
    assert {(result[1], result[2]) for result in results} == {(40, 1)}


def test_revocation_blocks_player_and_runtime_has_no_direct_learning_select() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set status='revoked' where id=%s", (entitlement,)
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.player.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.read_learning_player(%s,%s,null)", (buyer, entitlement)
            )
        connection.rollback()
        apply_context(connection, buyer, "learning.player.read")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select * from lesson_progress")


def test_concurrent_distinct_updates_have_one_version_winner() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])

    def update(index: int) -> str:
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                apply_context(connection, buyer, "learning.progress.write")
                connection.execute(
                    "select app_private.record_lesson_progress(%s,%s,%s,%s::smallint,0,%s,%s,%s)",
                    (
                        buyer,
                        entitlement,
                        lesson,
                        10 + index,
                        uuid4(),
                        hashlib.sha256(f"distinct-{index}".encode()).hexdigest(),
                        uuid4(),
                    ),
                )
            return "ok"
        except psycopg.errors.SerializationFailure:
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(update, range(8)))
    assert results.count("ok") == 1
    assert results.count("conflict") == 7


def test_one_hundred_buyers_write_and_resume_under_bounded_parallel_load() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    publication = UUID(manifest["publication"]["id"])
    lesson = UUID(manifest["lessons"][0]["lesson_id"])
    buyers = [identity_id(f"progress-capacity-{index:03d}") for index in range(100)]
    entitlements = [uuid5(NAMESPACE_URL, f"progress-entitlement-{buyer}") for buyer in buyers]
    with psycopg.connect(ADMIN_URL) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "insert into identity_user("
                "id,identity_issuer,identity_sub,email_normalized,email_verified) "
                "values(%s,%s,%s,%s,true)",
                [
                    (
                        buyer,
                        ISSUER,
                        f"progress-capacity-{index:03d}",
                        f"progress-capacity-{index:03d}@autonomia.site",
                    )
                    for index, buyer in enumerate(buyers)
                ],
            )
            cursor.executemany(
                "insert into buyer_entitlement(seller_organization_id,id,buyer_identity_user_id,"
                "catalog_product_id,publication_id,reference_key,source,status) "
                "values(%s,%s,%s,%s,%s,%s,'manual','active')",
                [
                    (seller, entitlement, buyer, product, publication, f"progress-{index:03d}")
                    for index, (buyer, entitlement) in enumerate(
                        zip(buyers, entitlements, strict=True)
                    )
                ],
            )
        connection.commit()

    def write_and_resume(item: tuple[UUID, UUID]) -> tuple[int, str]:
        buyer, entitlement = item
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, buyer, "learning.progress.write")
            progress = connection.execute(
                "select app_private.record_lesson_progress(%s,%s,%s,100::smallint,0,%s,%s,%s)",
                (
                    buyer,
                    entitlement,
                    lesson,
                    uuid4(),
                    hashlib.sha256(str(buyer).encode()).hexdigest(),
                    uuid4(),
                ),
            ).fetchone()
            connection.commit()
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, buyer, "learning.player.read")
            player = connection.execute(
                "select app_private.read_learning_player(%s,%s,null)",
                (buyer, entitlement),
            ).fetchone()
        assert progress is not None and player is not None
        return player[0]["course_progress_percent"], player[0]["resume_lesson_id"]

    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=FOUNDATION_DATABASE_WORKERS) as executor:
            results = list(executor.map(write_and_resume, zip(buyers, entitlements, strict=True)))
        elapsed = time.monotonic() - started
        assert results == [(20, manifest["lessons"][1]["lesson_id"])] * 100
        assert elapsed < 30
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                "delete from app_private.learning_progress_request "
                "where buyer_identity_user_id=any(%s)",
                (buyers,),
            )
            connection.execute(
                "delete from lesson_progress where buyer_identity_user_id=any(%s)", (buyers,)
            )
            connection.execute(
                "delete from course_learning_state where buyer_identity_user_id=any(%s)",
                (buyers,),
            )
            connection.execute(
                "delete from learning_enrollment where buyer_identity_user_id=any(%s)",
                (buyers,),
            )
            connection.execute(
                "delete from buyer_entitlement where buyer_identity_user_id=any(%s)", (buyers,)
            )
            connection.commit()
