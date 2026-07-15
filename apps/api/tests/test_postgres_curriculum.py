from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

USER_A = UUID("51000000-0000-4000-8000-000000000001")
USER_B = UUID("51000000-0000-4000-8000-000000000002")
SELLER_A = UUID("52000000-0000-4000-8000-000000000001")
SELLER_B = UUID("52000000-0000-4000-8000-000000000002")
PRODUCT_A = UUID("53000000-0000-4000-8000-000000000001")
PRODUCT_B = UUID("53000000-0000-4000-8000-000000000002")
COURSE_A = UUID("54000000-0000-4000-8000-000000000001")
COURSE_B = UUID("54000000-0000-4000-8000-000000000002")
OFFER_A = UUID("55000000-0000-4000-8000-000000000001")


def sign_context(
    actor: UUID, seller: UUID | None, capability: str, issued: int, expires: int
) -> str:
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    return hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


def apply_context(
    connection: psycopg.Connection[tuple[object, ...]], actor: UUID, seller: UUID, capability: str
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


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def required_row(row: tuple[Any, ...] | None) -> tuple[Any, ...]:
    assert row is not None
    return row


@pytest.fixture(scope="module", autouse=True)
def seed_curriculum() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            )
            values (%s,'https://identity.curriculum.test','curriculum-a','curriculum-a@example.test',true),
                   (%s,'https://identity.curriculum.test','curriculum-b','curriculum-b@example.test',true)
            """,
            (USER_A, USER_B),
        )
        connection.execute(
            """
            insert into seller_organization(id,slug,display_name,status)
            values (%s,'curriculum-a','Curriculum A','active'),
                   (%s,'curriculum-b','Curriculum B','active')
            """,
            (SELLER_A, SELLER_B),
        )
        connection.execute(
            """
            insert into seller_membership(seller_organization_id,identity_user_id,role,status)
            values (%s,%s,'seller_owner','active'),(%s,%s,'seller_owner','active')
            """,
            (SELLER_A, USER_A, SELLER_B, USER_B),
        )
        connection.execute(
            """
            insert into catalog_product(seller_organization_id,id,slug,title,description,status)
            values (%s,%s,'curriculum-product-a','Product A','Description A','draft'),
                   (%s,%s,'curriculum-product-b','Product B','Description B','draft')
            """,
            (SELLER_A, PRODUCT_A, SELLER_B, PRODUCT_B),
        )
        connection.execute(
            """
            insert into course(seller_organization_id,id,title,description,status,draft_version)
            values (%s,%s,'Course A','Course A description','draft',1),
                   (%s,%s,'Course B','Course B description','draft',1)
            """,
            (SELLER_A, COURSE_A, SELLER_B, COURSE_B),
        )
        connection.execute(
            """
            insert into product_course(seller_organization_id,catalog_product_id,course_id,position)
            values (%s,%s,%s,0),(%s,%s,%s,0)
            """,
            (SELLER_A, PRODUCT_A, COURSE_A, SELLER_B, PRODUCT_B, COURSE_B),
        )
        connection.execute(
            """
            insert into offer(
              seller_organization_id,id,catalog_product_id,title,summary,status,version
            )
            values (%s,%s,%s,'Offer A','Offer summary','draft',1)
            """,
            (SELLER_A, OFFER_A, PRODUCT_A),
        )
        connection.commit()


def create_module(version: int, key: UUID, body: str = "module") -> tuple[UUID, int, bool]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.create")
        row = connection.execute(
            "select * from app_private.create_course_module(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (USER_A, SELLER_A, PRODUCT_A, COURSE_A, "Module", version, key, digest(body), uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(tuple[UUID, int, bool], row)


def create_lesson(module: UUID, version: int, title: str = "Lesson") -> tuple[UUID, int, bool]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.create")
        row = connection.execute(
            "select * from app_private.create_course_lesson(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                USER_A,
                SELLER_A,
                PRODUCT_A,
                COURSE_A,
                module,
                title,
                f"Text for {title}",
                version,
                uuid4(),
                digest(title),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(tuple[UUID, int, bool], row)


def test_schema_rls_privileges_and_composite_fks() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select (select version_num from alembic_version),
              has_table_privilege('appsell_runtime','course_module','SELECT'),
              has_table_privilege('appsell_runtime','course_module','INSERT'),
              has_table_privilege('appsell_runtime','lesson','UPDATE'),
              has_table_privilege('appsell_runtime','app_private.curriculum_mutation_request','SELECT'),
              has_function_privilege('appsell_runtime',
                'app_private.create_course_module(uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid)','EXECUTE')
            """
        ).fetchone()
        tables = connection.execute(
            """
            select relname,relrowsecurity,relforcerowsecurity from pg_class
            where relname in ('course_module','lesson') order by relname
            """
        ).fetchall()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                "insert into course_module("
                "seller_organization_id,course_id,title,position"
                ") values(%s,%s,'Cross tenant',0)",
                (SELLER_A, COURSE_B),
            )
    assert state == ("0014_multi_asset_player", True, False, False, False, True)
    assert tables == [("course_module", True, True), ("lesson", True, True)]
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select count(*) from course_module").fetchone() == (0,)


def test_concurrent_retry_is_atomic_and_rls_isolates_tenants() -> None:
    key = uuid4()

    def attempt(_: int) -> tuple[UUID, int, bool]:
        return create_module(1, key, "concurrent-module")

    with ThreadPoolExecutor(max_workers=16) as executor:
        rows = list(executor.map(attempt, range(16)))
    assert len({row[0] for row in rows}) == 1
    assert [row[2] for row in rows].count(True) == 1
    assert [row[2] for row in rows].count(False) == 15
    module = rows[0][0]
    assert rows[0][1] == 2

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.read")
        assert connection.execute(
            "select id from course_module where id=%s", (module,)
        ).fetchall() == [(module,)]
        connection.rollback()
        apply_context(connection, USER_B, SELLER_B, "catalog.curriculum.read")
        assert (
            connection.execute("select id from course_module where id=%s", (module,)).fetchall()
            == []
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        create_module(2, key, "different-body")


def test_editor_reorder_full_set_version_and_archive() -> None:
    original_module = rows_module_id()
    first_module, version, _ = create_module(2, uuid4(), "first-module")
    second_module, version, _ = create_module(version, uuid4(), "second-module")
    first_lesson, version, _ = create_lesson(first_module, version, "Lesson One")
    second_lesson, version, _ = create_lesson(first_module, version, "Lesson Two")
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        resource_key = uuid4()
        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.update")
        version = required_row(
            connection.execute(
                "select course_version from app_private.update_course_module("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    COURSE_A,
                    first_module,
                    "Shared receipt title",
                    version,
                    resource_key,
                    digest("same-resource-body"),
                    uuid4(),
                ),
            ).fetchone()
        )[0]
        connection.commit()
        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.update")
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                "select * from app_private.update_course_module(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    COURSE_A,
                    second_module,
                    "Shared receipt title",
                    version,
                    resource_key,
                    digest("same-resource-body"),
                    uuid4(),
                ),
            )
        connection.rollback()

        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.reorder")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                "select * from app_private.reorder_course_modules(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    COURSE_A,
                    [second_module, second_module, first_module],
                    version,
                    uuid4(),
                    digest("bad-module-order"),
                    uuid4(),
                ),
            )
        connection.rollback()

        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.reorder")
        version = required_row(
            connection.execute(
                "select course_version from app_private.reorder_course_modules("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    COURSE_A,
                    [second_module, first_module, original_module],
                    version,
                    uuid4(),
                    digest("module-order"),
                    uuid4(),
                ),
            ).fetchone()
        )[0]
        connection.commit()

        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.reorder")
        version = required_row(
            connection.execute(
                "select course_version from app_private.reorder_module_lessons("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    COURSE_A,
                    first_module,
                    [second_lesson, first_lesson],
                    version,
                    uuid4(),
                    digest("lesson-order"),
                    uuid4(),
                ),
            ).fetchone()
        )[0]
        connection.commit()

        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.archive")
        version = required_row(
            connection.execute(
                "select course_version from app_private.archive_course_lesson("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    COURSE_A,
                    first_module,
                    first_lesson,
                    version,
                    uuid4(),
                    digest("archive-lesson"),
                    uuid4(),
                ),
            ).fetchone()
        )[0]
        connection.commit()

        apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.read")
        curriculum = required_row(
            connection.execute(
                "select curriculum from app_private.read_course_curriculum(%s,%s,%s,%s)",
                (USER_A, SELLER_A, PRODUCT_A, COURSE_A),
            ).fetchone()
        )[0]
    lessons = next(item for item in curriculum if item["id"] == str(first_module))["lessons"]
    assert [item["id"] for item in lessons] == [str(second_lesson)]
    assert lessons[0]["kind"] == "text"
    assert lessons[0]["content"] == "Text for Lesson Two"
    assert lessons[0]["status"] == "active"


def rows_module_id() -> UUID:
    """Return the module created by the concurrent retry without sharing mutable globals."""
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        return cast(
            UUID,
            required_row(
                connection.execute(
                    "select id from course_module where seller_organization_id=%s "
                    "and course_id=%s order by created_at limit 1",
                    (SELLER_A, COURSE_A),
                ).fetchone()
            )[0],
        )


def test_parallel_publications_lock_shared_courses_in_global_order() -> None:
    assert ADMIN_URL and RUNTIME_URL
    product_x, product_y = uuid4(), uuid4()
    course_x, course_y = uuid4(), uuid4()
    offer_x, offer_y = uuid4(), uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,%s,'Parallel X','','draft'),(%s,%s,%s,'Parallel Y','','draft')",
            (
                SELLER_A,
                product_x,
                f"parallel-{product_x}",
                SELLER_A,
                product_y,
                f"parallel-{product_y}",
            ),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status,draft_version) "
            "values(%s,%s,'Shared X','','draft',1),(%s,%s,'Shared Y','','draft',1)",
            (SELLER_A, course_x, SELLER_A, course_y),
        )
        connection.execute(
            "insert into product_course("
            "seller_organization_id,catalog_product_id,course_id,position) "
            "values(%s,%s,%s,0),(%s,%s,%s,1),(%s,%s,%s,0),(%s,%s,%s,1)",
            (
                SELLER_A,
                product_x,
                course_x,
                SELLER_A,
                product_x,
                course_y,
                SELLER_A,
                product_y,
                course_y,
                SELLER_A,
                product_y,
                course_x,
            ),
        )
        connection.execute(
            "insert into offer("
            "seller_organization_id,id,catalog_product_id,title,summary,status,version) "
            "values(%s,%s,%s,'Offer X','','draft',1),(%s,%s,%s,'Offer Y','','draft',1)",
            (SELLER_A, offer_x, product_x, SELLER_A, offer_y, product_y),
        )
        connection.commit()

    barrier = Barrier(2)

    def publish(product: UUID, offer: UUID) -> tuple[UUID, bool]:
        with psycopg.connect(RUNTIME_URL) as connection:
            connection.execute("set lock_timeout='5s'")
            apply_context(connection, USER_A, SELLER_A, "catalog.publication.create")
            barrier.wait(timeout=5)
            row = required_row(
                connection.execute(
                    "select publication_id,created from app_private.publish_product("
                    "%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
                    (
                        USER_A,
                        SELLER_A,
                        product,
                        offer,
                        json.dumps({str(course_x): 1, str(course_y): 1}),
                        uuid4(),
                        digest(f"parallel-{product}"),
                        uuid4(),
                    ),
                ).fetchone()
            )
            connection.commit()
            return cast(tuple[UUID, bool], row)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(publish, product_x, offer_x),
            executor.submit(publish, product_y, offer_y),
        ]
        rows = [future.result(timeout=15) for future in futures]
    assert len({row[0] for row in rows}) == 2
    assert all(row[1] for row in rows)


def test_publish_racing_curriculum_edit_never_persists_a_mixed_tree() -> None:
    assert ADMIN_URL and RUNTIME_URL
    product, course, offer, module = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,%s,'Race product','','draft')",
            (SELLER_A, product, f"race-{product}"),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status,draft_version) "
            "values(%s,%s,'Race course','','draft',1)",
            (SELLER_A, course),
        )
        connection.execute(
            "insert into product_course("
            "seller_organization_id,catalog_product_id,course_id,position) values(%s,%s,%s,0)",
            (SELLER_A, product, course),
        )
        connection.execute(
            "insert into offer("
            "seller_organization_id,id,catalog_product_id,title,summary,status,version) "
            "values(%s,%s,%s,'Race offer','','draft',1)",
            (SELLER_A, offer, product),
        )
        connection.execute(
            "insert into course_module(seller_organization_id,id,course_id,title,position) "
            "values(%s,%s,%s,'Before race',0)",
            (SELLER_A, module, course),
        )
        connection.commit()

    barrier = Barrier(2)

    def publish() -> tuple[str, Any]:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, USER_A, SELLER_A, "catalog.publication.create")
            barrier.wait(timeout=5)
            try:
                row = required_row(
                    connection.execute(
                        "select publication_id,created from app_private.publish_product("
                        "%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
                        (
                            USER_A,
                            SELLER_A,
                            product,
                            offer,
                            json.dumps({str(course): 1}),
                            uuid4(),
                            digest("race-publish"),
                            uuid4(),
                        ),
                    ).fetchone()
                )
                connection.commit()
                return "ok", row
            except psycopg.DatabaseError as error:
                connection.rollback()
                return "error", error.sqlstate

    def edit() -> tuple[str, Any]:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.update")
            barrier.wait(timeout=5)
            try:
                row = required_row(
                    connection.execute(
                        "select course_version from app_private.update_course_module("
                        "%s,%s,%s,%s,%s,%s,1,%s,%s,%s)",
                        (
                            USER_A,
                            SELLER_A,
                            product,
                            course,
                            module,
                            "After race",
                            uuid4(),
                            digest("race-edit"),
                            uuid4(),
                        ),
                    ).fetchone()
                )
                connection.commit()
                return "ok", row
            except psycopg.DatabaseError as error:
                connection.rollback()
                return "error", error.sqlstate

    with ThreadPoolExecutor(max_workers=2) as executor:
        publish_future, edit_future = executor.submit(publish), executor.submit(edit)
        publish_result, edit_result = (
            publish_future.result(timeout=15),
            edit_future.result(timeout=15),
        )
    assert edit_result[0] == "ok"
    assert publish_result[0] == "ok" or publish_result == ("error", "40001")
    with psycopg.connect(ADMIN_URL) as connection:
        current_title = required_row(
            connection.execute(
                "select title from course_module where seller_organization_id=%s and id=%s",
                (SELLER_A, module),
            ).fetchone()
        )[0]
        snapshots = connection.execute(
            "select snapshot from publication where seller_organization_id=%s "
            "and catalog_product_id=%s",
            (SELLER_A, product),
        ).fetchall()
    assert current_title == "After race"
    if snapshots:
        assert snapshots[0][0]["courses"][0]["modules"][0]["title"] == "Before race"


def test_all_curriculum_mutations_reject_cross_tenant_context() -> None:
    assert ADMIN_URL and RUNTIME_URL
    product, course, module, lesson = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,%s,'Isolation product','','draft')",
            (SELLER_A, product, f"isolation-{product}"),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status,draft_version) "
            "values(%s,%s,'Isolation course','','draft',1)",
            (SELLER_A, course),
        )
        connection.execute(
            "insert into product_course("
            "seller_organization_id,catalog_product_id,course_id,position) "
            "values(%s,%s,%s,0)",
            (SELLER_A, product, course),
        )
        connection.execute(
            "insert into course_module(seller_organization_id,id,course_id,title,position) "
            "values(%s,%s,%s,'Isolation module',0)",
            (SELLER_A, module, course),
        )
        connection.execute(
            "insert into lesson("
            "seller_organization_id,id,course_id,module_id,title,body_text,position) "
            "values(%s,%s,%s,%s,'Isolation lesson','text',0)",
            (SELLER_A, lesson, course, module),
        )
        connection.commit()

    common = (USER_B, SELLER_A, product, course)
    cases = [
        (
            "catalog.curriculum.create",
            "select * from app_private.create_course_module(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common + ("New module", 1, uuid4(), digest("cross-create-module"), uuid4()),
        ),
        (
            "catalog.curriculum.update",
            "select * from app_private.update_course_module(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common + (module, "Updated module", 1, uuid4(), digest("cross-update-module"), uuid4()),
        ),
        (
            "catalog.curriculum.archive",
            "select * from app_private.archive_course_module(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common + (module, 1, uuid4(), digest("cross-archive-module"), uuid4()),
        ),
        (
            "catalog.curriculum.reorder",
            "select * from app_private.reorder_course_modules(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common + ([module], 1, uuid4(), digest("cross-reorder-module"), uuid4()),
        ),
        (
            "catalog.curriculum.create",
            "select * from app_private.create_course_lesson(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common
            + (module, "New lesson", "text", 1, uuid4(), digest("cross-create-lesson"), uuid4()),
        ),
        (
            "catalog.curriculum.update",
            "select * from app_private.update_course_lesson(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common
            + (
                module,
                lesson,
                "Updated lesson",
                "text",
                1,
                uuid4(),
                digest("cross-update-lesson"),
                uuid4(),
            ),
        ),
        (
            "catalog.curriculum.archive",
            "select * from app_private.archive_course_lesson(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common + (module, lesson, 1, uuid4(), digest("cross-archive-lesson"), uuid4()),
        ),
        (
            "catalog.curriculum.reorder",
            "select * from app_private.reorder_module_lessons(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            common + (module, [lesson], 1, uuid4(), digest("cross-reorder-lesson"), uuid4()),
        ),
    ]
    for capability, statement, parameters in cases:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, USER_B, SELLER_A, capability)
            with pytest.raises(psycopg.DatabaseError) as rejected:
                connection.execute(statement, parameters)
            assert rejected.value.sqlstate == "28000"


def test_curriculum_enforces_total_and_byte_limits_at_max_plus_one() -> None:
    assert ADMIN_URL and RUNTIME_URL
    product = uuid4()
    module_course, module_lesson_course, course_lesson_course, title_course, content_course = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    module_lesson = uuid4()
    content_module = uuid4()
    course_limit_modules = [uuid4() for _ in range(6)]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,%s,'Limits product','','draft')",
            (SELLER_A, product, f"limits-{product}"),
        )
        courses = [
            module_course,
            module_lesson_course,
            course_lesson_course,
            title_course,
            content_course,
        ]
        for position, course in enumerate(courses):
            connection.execute(
                "insert into course("
                "seller_organization_id,id,title,description,status,draft_version) "
                "values(%s,%s,%s,'','draft',1)",
                (SELLER_A, course, f"Limit course {position}"),
            )
            connection.execute(
                "insert into product_course("
                "seller_organization_id,catalog_product_id,course_id,position) "
                "values(%s,%s,%s,%s)",
                (SELLER_A, product, course, position),
            )
        connection.execute(
            "insert into course_module(seller_organization_id,course_id,title,status,position) "
            "select %s,%s,'Archived module '||ordinality,'archived',1000+ordinality "
            "from generate_series(1,49) input(ordinality)",
            (SELLER_A, module_course),
        )
        connection.execute(
            "insert into course_module(seller_organization_id,id,course_id,title,position) "
            "values(%s,%s,%s,'Lesson module',0),(%s,%s,%s,'Content module',0)",
            (
                SELLER_A,
                module_lesson,
                module_lesson_course,
                SELLER_A,
                content_module,
                content_course,
            ),
        )
        connection.execute(
            "insert into lesson("
            "seller_organization_id,course_id,module_id,title,body_text,status,position) "
            "select %s,%s,%s,'Archived lesson '||ordinality,'','archived',1000+ordinality "
            "from generate_series(1,99) input(ordinality)",
            (SELLER_A, module_lesson_course, module_lesson),
        )
        for position, module in enumerate(course_limit_modules):
            connection.execute(
                "insert into course_module(seller_organization_id,id,course_id,title,position) "
                "values(%s,%s,%s,%s,%s)",
                (SELLER_A, module, course_lesson_course, f"Course cap {position}", position),
            )
            count = 99 if position == 0 else (0 if position == 5 else 100)
            if count:
                connection.execute(
                    "insert into lesson("
                    "seller_organization_id,course_id,module_id,title,body_text,status,position) "
                    "select %s,%s,%s,'Archived cap '||ordinality,'','archived',1000+ordinality "
                    "from generate_series(1,%s) input(ordinality)",
                    (SELLER_A, course_lesson_course, module, count),
                )
        connection.commit()

    def create_module_for(course: UUID, version: int, title: str) -> tuple[Any, ...]:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.create")
            row = required_row(
                connection.execute(
                    "select * from app_private.create_course_module(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        USER_A,
                        SELLER_A,
                        product,
                        course,
                        title,
                        version,
                        uuid4(),
                        digest(str(uuid4())),
                        uuid4(),
                    ),
                ).fetchone()
            )
            connection.commit()
            return row

    def create_lesson_for(
        course: UUID, module: UUID, version: int, content: str
    ) -> tuple[Any, ...]:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, USER_A, SELLER_A, "catalog.curriculum.create")
            row = required_row(
                connection.execute(
                    "select * from app_private.create_course_lesson("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        USER_A,
                        SELLER_A,
                        product,
                        course,
                        module,
                        "Valid lesson",
                        content,
                        version,
                        uuid4(),
                        digest(str(uuid4())),
                        uuid4(),
                    ),
                ).fetchone()
            )
            connection.commit()
            return row

    assert create_module_for(module_course, 1, "é" * 80)[1] == 2
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        create_module_for(module_course, 2, "Another module")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        create_module_for(title_course, 1, ("é" * 80) + "x")
    assert create_lesson_for(module_lesson_course, module_lesson, 1, "x")[1] == 2
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        create_lesson_for(module_lesson_course, module_lesson, 2, "x")
    target = course_limit_modules[5]
    assert create_lesson_for(course_lesson_course, target, 1, "x")[1] == 2
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        create_lesson_for(course_lesson_course, target, 2, "x")
    assert create_lesson_for(content_course, content_module, 1, "é" * 4096)[1] == 2
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        create_lesson_for(content_course, content_module, 2, ("é" * 4096) + "x")


def test_publication_v1_is_preserved_and_v2_is_deterministic() -> None:
    assert ADMIN_URL and RUNTIME_URL
    v1_id = uuid4()
    oversize_key = uuid4()
    oversize_modules = (uuid4(), uuid4())
    v1_snapshot = {
        "schema_version": 1,
        "seller_organization_id": str(SELLER_A),
        "product": {"id": str(PRODUCT_A), "slug": "curriculum-product-a"},
        "courses": [],
    }
    serialized = json.dumps(v1_snapshot, sort_keys=True, separators=(",", ":"))
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into publication(
              seller_organization_id,id,catalog_product_id,version,schema_version,
              source_sha256,snapshot,actor_identity_user_id)
            values(%s,%s,%s,1,1,%s,%s::jsonb,%s)
            """,
            (
                SELLER_A,
                v1_id,
                PRODUCT_A,
                hashlib.sha256(serialized.encode()).hexdigest(),
                serialized,
                USER_A,
            ),
        )
        connection.execute(
            "update catalog_product set status='published',published_snapshot_id=%s "
            "where seller_organization_id=%s and id=%s",
            (v1_id, SELLER_A, PRODUCT_A),
        )
        version = required_row(
            connection.execute(
                "select draft_version from course where seller_organization_id=%s and id=%s",
                (SELLER_A, COURSE_A),
            ).fetchone()
        )[0]
        next_position = required_row(
            connection.execute(
                "select coalesce(max(position),-1)+1 from course_module "
                "where seller_organization_id=%s and course_id=%s",
                (SELLER_A, COURSE_A),
            ).fetchone()
        )[0]
        connection.execute(
            "insert into course_module("
            "seller_organization_id,id,course_id,title,position"
            ") values(%s,%s,%s,'Oversize One',%s),(%s,%s,%s,'Oversize Two',%s)",
            (
                SELLER_A,
                oversize_modules[0],
                COURSE_A,
                next_position,
                SELLER_A,
                oversize_modules[1],
                COURSE_A,
                next_position + 1,
            ),
        )
        for module_id in oversize_modules:
            connection.execute(
                "insert into lesson("
                "seller_organization_id,course_id,module_id,title,body_text,position"
                ") select %s,%s,%s,'Oversize '||ordinality,repeat('x',8192),ordinality-1 "
                "from generate_series(1,80) as input(ordinality)",
                (SELLER_A, COURSE_A, module_id),
            )
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.publication.create")
        with pytest.raises(psycopg.DatabaseError) as oversized:
            connection.execute(
                "select * from app_private.publish_product(%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    OFFER_A,
                    json.dumps({str(COURSE_A): version}),
                    oversize_key,
                    digest("publish-oversize"),
                    uuid4(),
                ),
            )
        assert oversized.value.sqlstate == "P4221"
        connection.rollback()

    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select count(*) from publication where seller_organization_id=%s "
            "and catalog_product_id=%s",
            (SELLER_A, PRODUCT_A),
        ).fetchone() == (1,)
        assert connection.execute(
            "select count(*) from app_private.catalog_mutation_request "
            "where seller_organization_id=%s and operation='product.publish' "
            "and idempotency_key=%s",
            (SELLER_A, oversize_key),
        ).fetchone() == (0,)
        connection.execute(
            "delete from lesson where seller_organization_id=%s and module_id=any(%s)",
            (SELLER_A, list(oversize_modules)),
        )
        connection.execute(
            "delete from course_module where seller_organization_id=%s and id=any(%s)",
            (SELLER_A, list(oversize_modules)),
        )
        connection.commit()

    key = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.publication.create")
        row = connection.execute(
            "select * from app_private.publish_product(%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
            (
                USER_A,
                SELLER_A,
                PRODUCT_A,
                OFFER_A,
                json.dumps({str(COURSE_A): version}),
                key,
                digest("publish-v2"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
        apply_context(connection, USER_A, SELLER_A, "catalog.publication.create")
        replay = connection.execute(
            "select * from app_private.publish_product(%s,%s,%s,%s,1,1,%s::jsonb,%s,%s,%s)",
            (
                USER_A,
                SELLER_A,
                PRODUCT_A,
                OFFER_A,
                json.dumps({str(COURSE_A): version}),
                key,
                digest("publish-v2"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert row is not None and row[4] is True
    assert replay is not None and replay[:4] == row[:4] and replay[4] is False
    with psycopg.connect(ADMIN_URL) as connection:
        v1 = connection.execute(
            "select schema_version,snapshot from publication where id=%s", (v1_id,)
        ).fetchone()
        v2 = connection.execute(
            "select schema_version,snapshot,source_sha256,"
            "encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex') "
            "from publication where id=%s",
            (row[0],),
        ).fetchone()
    assert v2 is not None
    assert v1 == (1, v1_snapshot)
    assert v2[0] == 3 and v2[1]["schema_version"] == 3
    assert v2[2] == v2[3]
    published_module = next(
        module for module in v2[1]["courses"][0]["modules"] if module["lessons"]
    )
    assert published_module["status"] == "active"
    assert published_module["lessons"][0]["kind"] == "text"
    assert "content" in published_module["lessons"][0]
