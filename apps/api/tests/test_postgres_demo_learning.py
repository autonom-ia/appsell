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

from appsell_api.reset_demo_tenant import load_manifest, reset_demo

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

ISSUER = "https://identity.demo.test.autonomia.site"
OTHER_USER = UUID("71000000-0000-4000-8000-000000000099")
ACTIVE_SUPERADMIN = UUID("71000000-0000-4000-8000-000000000100")
FOUNDATION_DATABASE_WORKERS = 12


def identity_id(subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{ISSUER}\0{subject}")


def apply_context(
    connection: psycopg.Connection[Any],
    actor: UUID,
    capability: str,
    seller: UUID | None = None,
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


@pytest.fixture(scope="module", autouse=True)
def seed_demo() -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user("
            "id,identity_issuer,identity_sub,email_normalized,email_verified) "
            "values(%s,%s,'other-demo-user','other.demo@autonomia.site',true)",
            (OTHER_USER, ISSUER),
        )
        connection.execute(
            "insert into identity_user("
            "id,identity_issuer,identity_sub,email_normalized,email_verified) "
            "values(%s,%s,'active-superadmin','commercial-test@autonomia.site',true)",
            (ACTIVE_SUPERADMIN, ISSUER),
        )
        connection.commit()
    reset_once(manifest, manifest_sha)


def _seed_learning_entitlement(
    connection: psycopg.Connection[Any], manifest: dict[str, Any]
) -> None:
    connection.execute(
        """
        insert into buyer_entitlement(
          seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
          publication_id,reference_key,source,status,progress_percent
        ) values(%s,%s,%s,%s,%s,'pytest-learning-fixture','demo_seed','active',0)
        on conflict(seller_organization_id,buyer_identity_user_id,catalog_product_id)
        do update set publication_id=excluded.publication_id,status='active',
          progress_percent=0,starts_at=clock_timestamp(),expires_at=null,
          updated_at=clock_timestamp()
        """,
        (
            UUID(manifest["seller"]["id"]),
            UUID(manifest["entitlement"]["id"]),
            identity_id(manifest["personas"]["buyer"]["subject"]),
            UUID(manifest["product"]["id"]),
            UUID(manifest["publication"]["id"]),
        ),
    )


def reset_once(
    manifest: dict[str, Any], manifest_sha: str, *, grant_entitlement: bool = True
) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        if grant_entitlement:
            _seed_learning_entitlement(connection, manifest)
        connection.commit()


def test_reset_is_repeatable_concurrent_and_does_not_duplicate_graph() -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    contaminated_seller = UUID("72000000-0000-4000-8000-000000000099")
    extra_product = UUID("72000000-0000-4000-8000-000000000001")
    extra_course = UUID("72000000-0000-4000-8000-000000000002")
    extra_offer = UUID("72000000-0000-4000-8000-000000000003")
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,'contaminated','Contaminated','Must be removed','draft')",
            (seller, extra_product),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status) "
            "values(%s,%s,'Contaminated','Must be removed','draft')",
            (seller, extra_course),
        )
        connection.execute(
            "insert into product_course("
            "seller_organization_id,catalog_product_id,course_id,position) "
            "values(%s,%s,%s,1)",
            (seller, extra_product, extra_course),
        )
        connection.execute(
            "insert into offer(seller_organization_id,id,catalog_product_id,title,summary,status) "
            "values(%s,%s,%s,'Contaminated','Must be removed','draft')",
            (seller, extra_offer, extra_product),
        )
        connection.execute(
            "insert into platform_grant("
            "id,identity_user_id,role,status,revoked_at) "
            "values('72000000-0000-4000-8000-000000000004',%s,"
            "'platform_superadmin','revoked',clock_timestamp())",
            (OTHER_USER,),
        )
        connection.execute(
            "insert into platform_grant(id,identity_user_id,role,status) "
            "values('72000000-0000-4000-8000-000000000005',%s,"
            "'platform_superadmin','active')",
            (ACTIVE_SUPERADMIN,),
        )
        connection.execute(
            "insert into seller_membership("
            "seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_admin','active')",
            (seller, OTHER_USER),
        )
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'demo-admin-contamination','Must remain inaccessible','active')",
            (contaminated_seller,),
        )
        connection.execute(
            "insert into seller_membership("
            "seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_owner','active'),(%s,%s,'seller_admin','active')",
            (contaminated_seller, admin, contaminated_seller, buyer),
        )
        connection.execute(
            "insert into platform_grant(id,identity_user_id,role,status,revoked_at) "
            "values('72000000-0000-4000-8000-000000000006',%s,"
            "'platform_superadmin','revoked',clock_timestamp())",
            (admin,),
        )
        connection.commit()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda _: reset_once(manifest, manifest_sha, grant_entitlement=False),
                range(8),
            )
        )
    product = UUID(manifest["product"]["id"])
    with psycopg.connect(ADMIN_URL) as connection:
        counts = connection.execute(
            """
            select
              (select count(*) from seller_organization where id=%s),
              (select count(*) from catalog_product
                where seller_organization_id=%s and id=%s),
              (select count(*) from catalog_product where seller_organization_id=%s),
              (select count(*) from course where seller_organization_id=%s),
              (select count(*) from offer where seller_organization_id=%s),
              (select count(*) from seller_membership where seller_organization_id=%s),
              (select count(*) from course_module where seller_organization_id=%s),
              (select count(*) from lesson where seller_organization_id=%s),
              (select count(*) from learning_asset where seller_organization_id=%s),
              (select count(*) from buyer_entitlement where seller_organization_id=%s),
              (select count(*) from publication
                where seller_organization_id=%s and catalog_product_id=%s),
              (select count(*) from identity_user where id=%s)
            """,
            (
                seller,
                seller,
                product,
                seller,
                seller,
                seller,
                seller,
                seller,
                seller,
                seller,
                seller,
                seller,
                product,
                OTHER_USER,
            ),
        ).fetchone()
        memberships = connection.execute(
            "select identity_user_id,role,status from seller_membership "
            "where seller_organization_id=%s order by identity_user_id",
            (seller,),
        ).fetchall()
        invalid_memberships = connection.execute(
            """
            select count(*) from seller_membership membership
            where membership.seller_organization_id=%s and not (
              membership.identity_user_id=%s and membership.role='seller_owner'
                and membership.status='active'
            )
            """,
            (seller, admin),
        ).fetchone()
        demo_persona_memberships = connection.execute(
            "select seller_organization_id,identity_user_id,role,status "
            "from seller_membership where identity_user_id in (%s,%s) "
            "order by seller_organization_id,identity_user_id",
            (admin, buyer),
        ).fetchall()
        demo_persona_grants = connection.execute(
            "select count(*) from platform_grant where identity_user_id in (%s,%s)",
            (admin, buyer),
        ).fetchone()
    assert counts == (1, 1, 1, 1, 1, 1, 1, 5, 6, 0, 1, 1)
    assert invalid_memberships == (0,)
    assert demo_persona_memberships == [(seller, admin, "seller_owner", "active")]
    assert demo_persona_grants == (0,)
    assert (OTHER_USER, "seller_admin", "active") not in memberships
    assert (ACTIVE_SUPERADMIN, "seller_admin", "active") not in memberships
    with psycopg.connect(ADMIN_URL) as connection:
        _seed_learning_entitlement(connection, manifest)
        connection.commit()


def test_buyer_reads_only_active_entitlement_and_published_snapshot() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "library.read")
        library = connection.execute(
            "select app_private.read_buyer_library(%s)", (buyer,)
        ).fetchone()
        connection.rollback()
        apply_context(connection, admin, "library.read")
        admin_library = connection.execute(
            "select app_private.read_buyer_library(%s)", (admin,)
        ).fetchone()
    assert library is not None
    assert len(library[0]) == 1
    item = library[0][0]
    assert item["product"]["id"] == manifest["product"]["id"]
    assert item["product"]["publication_version"] == 1
    assert "snapshot" not in item
    assert "assets" not in item
    assert admin_library == ([],)


def test_public_free_access_is_idempotent_and_creates_learning_enrollment() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    buyer = OTHER_USER
    idempotency_key = uuid4()
    request_id = uuid4()
    body_sha256 = hashlib.sha256(b"public-free-access-test").hexdigest()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "library.enroll")
        first = connection.execute(
            "select entitlement_id,created from app_private.grant_public_product_access("
            "%s,%s,%s,%s,%s,%s)",
            (buyer, seller, product, idempotency_key, body_sha256, request_id),
        ).fetchone()
        replay = connection.execute(
            "select entitlement_id,created from app_private.grant_public_product_access("
            "%s,%s,%s,%s,%s,%s)",
            (buyer, seller, product, idempotency_key, body_sha256, request_id),
        ).fetchone()
        assert first is not None
        assert first[1] is True
        first_entitlement_id = first[0]
        enrollment_row = connection.execute(
            "select count(*) from learning_enrollment where buyer_identity_user_id=%s "
            "and entitlement_id=%s",
            (buyer, first_entitlement_id),
        ).fetchone()
        assert enrollment_row is not None
        enrollment_count = enrollment_row[0]
        connection.rollback()
    assert replay == (first_entitlement_id, False)
    assert enrollment_count >= 1


def test_asset_authorization_binds_identity_entitlement_and_manifest() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    admin = identity_id(manifest["personas"]["admin"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    asset = manifest["lessons"][0]
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "library.asset.read")
        row = connection.execute(
            "select seed_key,file_name,byte_size,sha256 "
            "from app_private.authorize_learning_asset(%s,%s,%s)",
            (buyer, entitlement, UUID(asset["asset_id"])),
        ).fetchone()
        connection.rollback()
        apply_context(connection, admin, "library.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_learning_asset(%s,%s,%s)",
                (admin, entitlement, UUID(asset["asset_id"])),
            )
    assert row == (
        asset["slug"],
        f"{asset['slug']}.pdf",
        asset["bytes"],
        asset["sha256"],
    )


def test_buyer_library_and_asset_reject_seller_scoped_context() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    seller = UUID(manifest["seller"]["id"])
    entitlement = UUID(manifest["entitlement"]["id"])
    asset = UUID(manifest["lessons"][0]["asset_id"])
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "library.read", seller)
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute("select app_private.read_buyer_library(%s)", (buyer,))
        connection.rollback()
        apply_context(connection, buyer, "library.asset.read", seller)
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_learning_asset(%s,%s,%s)",
                (buyer, entitlement, asset),
            )


def test_buyer_library_and_asset_fail_closed_without_request_context() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    asset = UUID(manifest["lessons"][0]["asset_id"])
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute("select app_private.read_buyer_library(%s)", (buyer,))
        connection.rollback()
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_learning_asset(%s,%s,%s)",
                (buyer, entitlement, asset),
            )


def test_suspended_seller_cannot_serve_a_learning_asset() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, manifest_sha = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    asset = UUID(manifest["lessons"][0]["asset_id"])
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update seller_organization set status='suspended' where id=%s", (seller,)
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "library.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_learning_asset(%s,%s,%s)",
                (buyer, entitlement, asset),
            )
    reset_once(manifest, manifest_sha)


def test_runtime_cannot_mutate_entitlements_or_assets() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        privileges = connection.execute(
            """
            select
              has_table_privilege('appsell_runtime','buyer_entitlement','SELECT'),
              has_table_privilege('appsell_runtime','buyer_entitlement','INSERT'),
              has_table_privilege('appsell_runtime','learning_asset','SELECT'),
              has_table_privilege('appsell_runtime','learning_asset','UPDATE'),
              has_table_privilege(
                'appsell_runtime','app_private.demo_tenant_registry','SELECT'
              ),
              has_function_privilege(
                'appsell_runtime','app_private.read_buyer_library(uuid)','EXECUTE'
              )
            """
        ).fetchone()
    assert privileges == (True, False, False, False, False, True)


def test_revoked_entitlement_fails_closed_for_library_and_asset() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, manifest_sha = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    asset = UUID(manifest["lessons"][0]["asset_id"])
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set status='revoked' "
            "where seller_organization_id=%s and buyer_identity_user_id=%s",
            (seller, buyer),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "library.read")
        assert connection.execute(
            "select app_private.read_buyer_library(%s)", (buyer,)
        ).fetchone() == ([],)
        connection.rollback()
        apply_context(connection, buyer, "library.asset.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.authorize_learning_asset(%s,%s,%s)",
                (buyer, entitlement, asset),
            )
    reset_once(manifest, manifest_sha)


def test_one_hundred_buyers_read_their_entitlement_under_bounded_parallel_load() -> None:
    assert ADMIN_URL and RUNTIME_URL
    manifest, _ = load_manifest()
    seller = UUID(manifest["seller"]["id"])
    product = UUID(manifest["product"]["id"])
    buyers = [uuid5(NAMESPACE_URL, f"{ISSUER}\0capacity-buyer-{index:03d}") for index in range(100)]
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
                        f"capacity-buyer-{index:03d}",
                        f"capacity-buyer-{index:03d}@autonomia.site",
                    )
                    for index, buyer in enumerate(buyers)
                ],
            )
            cursor.executemany(
                "insert into buyer_entitlement("
                "seller_organization_id,id,buyer_identity_user_id,catalog_product_id,"
                "publication_id,reference_key,source,status,progress_percent) "
                "values(%s,%s,%s,%s,%s,%s,'manual','active',0)",
                [
                    (
                        seller,
                        uuid5(NAMESPACE_URL, f"capacity-entitlement-{buyer}"),
                        buyer,
                        product,
                        UUID(manifest["publication"]["id"]),
                        f"capacity-buyer-{index:03d}",
                    )
                    for index, buyer in enumerate(buyers)
                ],
            )
        connection.commit()

    def read_library(buyer: UUID) -> tuple[int, str]:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, buyer, "library.read")
            row = connection.execute(
                "select app_private.read_buyer_library(%s)", (buyer,)
            ).fetchone()
            assert row is not None
            return len(row[0]), row[0][0]["product"]["id"]

    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=FOUNDATION_DATABASE_WORKERS) as executor:
            results = list(executor.map(read_library, buyers))
        elapsed = time.monotonic() - started
        assert results == [(1, str(product))] * 100
        assert elapsed < 30
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                "delete from course_learning_state where seller_organization_id=%s "
                "and buyer_identity_user_id=any(%s)",
                (seller, buyers),
            )
            connection.execute(
                "delete from learning_enrollment where seller_organization_id=%s "
                "and buyer_identity_user_id=any(%s)",
                (seller, buyers),
            )
            connection.execute(
                "delete from buyer_entitlement where seller_organization_id=%s "
                "and buyer_identity_user_id=any(%s)",
                (seller, buyers),
            )
            connection.execute("delete from identity_user where id=any(%s)", (buyers,))
            connection.commit()
